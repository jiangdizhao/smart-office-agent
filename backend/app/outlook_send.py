from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.models import ToolResult
from app.outlook_drafts import (
    _account_email,
    _exception_details,
    _find_sender_account,
    _outlook_application,
    _recipient_smtp_addresses,
)
from app.presentation_config import EmailRecipient, presentation_config
from app.state_store import state_store

LOGGER = logging.getLogger(__name__)

DRAFT_ONLY_NOTICE_ZH = "该邮件目前仅保存为 Outlook 草稿，尚未发送。"
DRAFT_ONLY_NOTICE_EN = (
    "This message has only been saved as an Outlook draft and has not been sent."
)


def _latest_unsent_verified_draft(
    recipient_key: str | None = None,
) -> dict[str, Any] | None:
    tasks = sorted(state_store.list_tasks(), key=lambda task: task.updated_at, reverse=True)
    sent_entry_ids: set[str] = set()

    for task in tasks:
        for step in task.steps:
            result = step.result
            if result is None or result.tool_name != "outlook_send_approved_draft":
                continue
            if result.data.get("sent") is True:
                entry_id = str(result.data.get("source_outlook_draft_entry_id") or "")
                if entry_id:
                    sent_entry_ids.add(entry_id)

    requested_key = recipient_key.casefold() if recipient_key else None
    for task in tasks:
        for step in reversed(task.steps):
            result = step.result
            if result is None or result.tool_name != "outlook_create_summary_draft":
                continue
            data = result.data
            entry_id = str(data.get("outlook_draft_entry_id") or "")
            draft_key = str(
                data.get("recipient_key") or presentation_config.default_recipient_key
            ).casefold()
            if requested_key and draft_key != requested_key:
                continue
            if (
                result.ok
                and data.get("outlook_draft_verified") is True
                and data.get("sent") is False
                and entry_id
                and entry_id not in sent_entry_ids
            ):
                return {
                    "task_id": task.task_id,
                    "step_id": step.step_id,
                    "entry_id": entry_id,
                    "store_id": str(data.get("outlook_draft_store_id") or ""),
                    "sender_email": str(data.get("sender_account_email") or ""),
                    "recipient_key": draft_key,
                    "recipient_name": str(data.get("recipient_name") or ""),
                    "recipient_email": str(data.get("recipient_email") or ""),
                    "subject": str(data.get("subject") or ""),
                }
    return None


def _failure(
    *,
    stage: str,
    message: str,
    draft: dict[str, Any] | None,
    recipient: EmailRecipient | None = None,
    requested_recipient_key: str | None = None,
    exc: Exception | None = None,
) -> ToolResult:
    sender = presentation_config.outlook_sender_email.strip()
    details: dict[str, Any] = {
        "execution_mode": "failed",
        "requested_state": {
            "outlook_email_sent": True,
            "recipient_key": requested_recipient_key,
        },
        "failure_stage": stage,
        "source_outlook_draft_entry_id": (draft or {}).get("entry_id"),
        "source_outlook_draft_store_id": (draft or {}).get("store_id"),
        "sender_account_email": sender,
        "recipient_key": (
            recipient.key
            if recipient
            else (draft or {}).get("recipient_key") or requested_recipient_key
        ),
        "recipient_name": (
            recipient.name if recipient else (draft or {}).get("recipient_name")
        ),
        "recipient_email": (
            recipient.email if recipient else (draft or {}).get("recipient_email")
        ),
        "approval_gated_email_send_enabled": True,
        "unrestricted_email_send_enabled": False,
        "draft_notice_removed": False,
        "send_invoked": False,
        "sent": False,
    }
    if exc is not None:
        details.update(_exception_details(exc))

    LOGGER.error(
        "OUTLOOK_SEND_FAILURE stage=%s sender=%s recipient_key=%s recipient=%s entry_id=%s error_type=%s error=%s hresult=%s args=%s",
        stage,
        sender,
        details.get("recipient_key"),
        details.get("recipient_email"),
        details.get("source_outlook_draft_entry_id") or "none",
        details.get("error_type", "none"),
        details.get("error", message),
        details.get("hresult"),
        details.get("args"),
        exc_info=exc is not None,
    )
    return ToolResult(
        tool_name="outlook_send_approved_draft",
        ok=False,
        message=message,
        data=details,
        raw={
            "failure_stage": stage,
            "recipient_key": details.get("recipient_key"),
            "approval_gated_email_send_enabled": True,
            "unrestricted_email_send_enabled": False,
            "sent": False,
        },
    )


def send_latest_outlook_draft(recipient_key: str | None = None) -> ToolResult:
    requested_recipient_key = recipient_key or None
    requested_recipient: EmailRecipient | None = None
    if requested_recipient_key:
        try:
            requested_recipient = presentation_config.resolve_recipient(requested_recipient_key)
            requested_recipient_key = requested_recipient.key
        except ValueError as exc:
            return _failure(
                stage="recipient_allowlist",
                message=str(exc),
                draft=None,
                requested_recipient_key=requested_recipient_key,
                exc=exc,
            )

    draft = _latest_unsent_verified_draft(requested_recipient_key)
    sender_email = presentation_config.outlook_sender_email.strip()
    stage = "preflight"

    if draft is None:
        target = f" for recipient {requested_recipient_key}" if requested_recipient_key else ""
        return _failure(
            stage="draft_lookup",
            message=(
                f"No verified unsent Outlook draft is available{target}. Create and approve "
                "a new Outlook draft before requesting send."
            ),
            draft=None,
            recipient=requested_recipient,
            requested_recipient_key=requested_recipient_key,
        )

    try:
        recipient = presentation_config.resolve_recipient(draft["recipient_key"])
    except ValueError as exc:
        return _failure(
            stage="recipient_allowlist",
            message=str(exc),
            draft=draft,
            requested_recipient_key=draft.get("recipient_key"),
            exc=exc,
        )

    recipient_email = recipient.email
    LOGGER.info(
        "OUTLOOK_SEND_START sender=%s recipient_key=%s recipient=%s entry_id=%s",
        sender_email,
        recipient.key,
        recipient_email,
        draft.get("entry_id") or "none",
    )

    if not sender_email or sender_email.casefold() == recipient_email.casefold():
        return _failure(
            stage="address_configuration",
            message="Configured Outlook sender and recipient must be present and different.",
            draft=draft,
            recipient=recipient,
            requested_recipient_key=recipient.key,
        )
    if (
        draft["sender_email"].casefold() != sender_email.casefold()
        or draft["recipient_email"].casefold() != recipient_email.casefold()
        or draft["recipient_key"].casefold() != recipient.key.casefold()
    ):
        return _failure(
            stage="draft_configuration_match",
            message="The selected draft no longer matches the configured sender and recipient alias.",
            draft=draft,
            recipient=recipient,
            requested_recipient_key=recipient.key,
        )
    if os.name != "nt":
        return _failure(
            stage="platform_check",
            message="Classic Outlook COM sending is available only on Windows.",
            draft=draft,
            recipient=recipient,
            requested_recipient_key=recipient.key,
        )

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        return _failure(
            stage="dependency_import",
            message="pywin32 is required for Classic Outlook sending.",
            draft=draft,
            recipient=recipient,
            requested_recipient_key=recipient.key,
            exc=exc,
        )

    pythoncom.CoInitialize()
    try:
        stage = "outlook_connection"
        outlook, connection_mode = _outlook_application(win32com.client)
        namespace = outlook.GetNamespace("MAPI")

        stage = "sender_account_lookup"
        sender_account, detected_accounts = _find_sender_account(namespace, sender_email)

        stage = "draft_reopen"
        entry_id = draft["entry_id"]
        store_id = draft["store_id"]
        mail = (
            namespace.GetItemFromID(entry_id, store_id)
            if store_id
            else namespace.GetItemFromID(entry_id)
        )
        if bool(getattr(mail, "Sent", False)):
            raise RuntimeError("The selected Outlook item has already been sent.")

        draft_parent = getattr(mail, "Parent", None)
        draft_parent_entry_id = str(getattr(draft_parent, "EntryID", "") or "")
        observed_subject = str(getattr(mail, "Subject", "") or "")
        observed_recipients = _recipient_smtp_addresses(mail)
        normalized_recipients = [address.casefold() for address in observed_recipients]
        if normalized_recipients != [recipient_email.casefold()]:
            raise RuntimeError(
                "The approved draft must contain exactly the selected allowlisted recipient "
                "and no additional To, CC, or BCC recipients. "
                f"Expected {recipient_email}; observed {observed_recipients}."
            )

        stage = "sender_account_assignment"
        mail.SendUsingAccount = sender_account
        assigned_sender = _account_email(getattr(mail, "SendUsingAccount", None))
        if assigned_sender and assigned_sender.casefold() != sender_email.casefold():
            raise RuntimeError(
                f"Outlook bound the message to {assigned_sender}, not {sender_email}."
            )

        stage = "draft_notice_removal"
        original_body = str(getattr(mail, "Body", "") or "")
        cleaned_body = original_body.replace(DRAFT_ONLY_NOTICE_ZH, "").replace(
            DRAFT_ONLY_NOTICE_EN,
            "",
        )
        mail.Body = cleaned_body
        mail.Save()

        verified_mail = (
            namespace.GetItemFromID(entry_id, store_id)
            if store_id
            else namespace.GetItemFromID(entry_id)
        )
        verified_body = str(getattr(verified_mail, "Body", "") or "")
        if DRAFT_ONLY_NOTICE_ZH in verified_body or DRAFT_ONLY_NOTICE_EN in verified_body:
            raise RuntimeError("The draft-only notice could not be removed before sending.")
        verified_recipients = _recipient_smtp_addresses(verified_mail)
        if [address.casefold() for address in verified_recipients] != normalized_recipients:
            raise RuntimeError("The recipient list changed while preparing the approved send.")

        stage = "send_invocation"
        verified_mail.SendUsingAccount = sender_account
        verified_mail.Send()

        stage = "send_acceptance_verification"
        send_accepted = False
        acceptance_evidence = ""
        for _ in range(20):
            try:
                observed_item = (
                    namespace.GetItemFromID(entry_id, store_id)
                    if store_id
                    else namespace.GetItemFromID(entry_id)
                )
            except Exception:
                send_accepted = True
                acceptance_evidence = "original_entry_id_unavailable"
                break

            try:
                if bool(getattr(observed_item, "Sent", False)):
                    send_accepted = True
                    acceptance_evidence = "sent_property_true"
                    break
            except Exception:
                pass

            try:
                observed_parent = getattr(observed_item, "Parent", None)
                observed_parent_entry_id = str(
                    getattr(observed_parent, "EntryID", "") or ""
                )
                if (
                    draft_parent_entry_id
                    and observed_parent_entry_id
                    and observed_parent_entry_id != draft_parent_entry_id
                ):
                    send_accepted = True
                    acceptance_evidence = "moved_out_of_original_drafts_folder"
                    break
            except Exception:
                pass
            time.sleep(0.25)

        if not send_accepted:
            raise RuntimeError(
                "Outlook Send() returned, but the item still appeared as an unsent item in "
                "the original Drafts folder."
            )

        LOGGER.info(
            "OUTLOOK_SEND_SUCCESS sender=%s recipient_key=%s recipient=%s entry_id=%s connection_mode=%s detected_accounts=%s notice_removed=%s acceptance_evidence=%s",
            sender_email,
            recipient.key,
            recipient_email,
            entry_id,
            connection_mode,
            detected_accounts,
            True,
            acceptance_evidence,
        )
        return ToolResult(
            tool_name="outlook_send_approved_draft",
            ok=True,
            message=(
                f"Outlook accepted the approved email send from {sender_email} to "
                f"{recipient.name} <{recipient_email}>."
            ),
            expected_process_names=["OUTLOOK.EXE"],
            expected_window_keywords=["Outlook"],
            data={
                "execution_mode": "real",
                "requested_state": {
                    "outlook_email_sent": True,
                    "recipient_key": recipient.key,
                },
                "source_outlook_draft_entry_id": entry_id,
                "source_outlook_draft_store_id": store_id,
                "sender_account_email": sender_email,
                "recipient_key": recipient.key,
                "recipient_name": recipient.name,
                "recipient_email": recipient_email,
                "subject": observed_subject,
                "verified_recipient_addresses": verified_recipients,
                "outlook_connection_mode": connection_mode,
                "detected_outlook_accounts": detected_accounts,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
                "draft_notice_removed": True,
                "send_invoked": True,
                "send_acceptance_evidence": acceptance_evidence,
                "sent": True,
                "delivery_confirmed": False,
            },
            raw={
                "source_outlook_draft_entry_id": entry_id,
                "recipient_key": recipient.key,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
                "draft_notice_removed": True,
                "send_invoked": True,
                "send_acceptance_evidence": acceptance_evidence,
                "sent": True,
                "delivery_confirmed": False,
            },
        )
    except Exception as exc:
        return _failure(
            stage=stage,
            message=f"Approved Outlook email could not be sent: {type(exc).__name__}: {exc}",
            draft=draft,
            recipient=recipient,
            requested_recipient_key=recipient.key,
            exc=exc,
        )
    finally:
        pythoncom.CoUninitialize()
