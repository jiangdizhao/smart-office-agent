from __future__ import annotations

import gc
import logging
import os
import time
import uuid
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

OUTLOOK_OUTBOX_FOLDER = 4
OUTLOOK_SENT_MAIL_FOLDER = 5
OUTLOOK_INBOX_FOLDER = 6
SMART_OFFICE_SEND_TOKEN_DASL = (
    "http://schemas.microsoft.com/mapi/string/"
    "{00020329-0000-0000-C000-000000000046}/SmartOfficeSendToken"
)
DEFAULT_SENT_ITEMS_WAIT_SECONDS = 45.0
SENT_ITEMS_POLL_SECONDS = 0.5
SENT_ITEMS_SCAN_LIMIT = 60


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
    draft_notice_removed: bool = False,
    send_invoked: bool = False,
    outbox_observed: bool = False,
    outlook_window_ensured: bool = False,
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
        "draft_notice_removed": draft_notice_removed,
        "send_invoked": send_invoked,
        "outbox_observed": outbox_observed,
        "outlook_window_ensured": outlook_window_ensured,
        "sent": False,
    }
    if exc is not None:
        details.update(_exception_details(exc))

    LOGGER.error(
        "OUTLOOK_SEND_FAILURE stage=%s sender=%s recipient_key=%s recipient=%s entry_id=%s send_invoked=%s outbox_observed=%s error_type=%s error=%s hresult=%s args=%s",
        stage,
        sender,
        details.get("recipient_key"),
        details.get("recipient_email"),
        details.get("source_outlook_draft_entry_id") or "none",
        send_invoked,
        outbox_observed,
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
            "draft_notice_removed": draft_notice_removed,
            "send_invoked": send_invoked,
            "outbox_observed": outbox_observed,
            "sent": False,
        },
    )


def _account_default_folder(namespace: Any, account: Any, folder_kind: int) -> Any:
    try:
        delivery_store = account.DeliveryStore
        if delivery_store is not None:
            return delivery_store.GetDefaultFolder(folder_kind)
    except Exception:
        pass
    return namespace.GetDefaultFolder(folder_kind)


def _ensure_visible_outlook_explorer(outlook: Any, namespace: Any, account: Any) -> tuple[Any | None, bool]:
    """Keep a user-visible Outlook Explorer open after the draft Inspector closes.

    A draft can be the only visible Outlook window. MailItem.Send() closes that
    Inspector. If Outlook was started only for COM automation, releasing the last
    automation reference can then end OUTLOOK.EXE before transport finishes.
    """

    try:
        if int(outlook.Explorers.Count) > 0:
            return None, False
    except Exception:
        pass

    inbox = _account_default_folder(namespace, account, OUTLOOK_INBOX_FOLDER)
    explorer = inbox.GetExplorer()
    explorer.Display()
    time.sleep(0.6)
    return explorer, True


def _set_send_token(mail: Any, send_token: str) -> None:
    mail.PropertyAccessor.SetProperty(SMART_OFFICE_SEND_TOKEN_DASL, send_token)
    observed = str(
        mail.PropertyAccessor.GetProperty(SMART_OFFICE_SEND_TOKEN_DASL) or ""
    )
    if observed != send_token:
        raise RuntimeError("Outlook did not persist the Smart Office send correlation token.")


def _item_send_token(item: Any) -> str:
    try:
        return str(
            item.PropertyAccessor.GetProperty(SMART_OFFICE_SEND_TOKEN_DASL) or ""
        )
    except Exception:
        return ""


def _item_matches_send(
    item: Any,
    *,
    send_token: str,
    subject: str,
    recipient_email: str,
) -> bool:
    token = _item_send_token(item)
    if token:
        if token != send_token:
            return False
    else:
        try:
            if str(getattr(item, "Subject", "") or "") != subject:
                return False
        except Exception:
            return False

    try:
        recipients = [address.casefold() for address in _recipient_smtp_addresses(item)]
    except Exception:
        return False
    return recipients == [recipient_email.casefold()]


def _find_matching_item(
    folder: Any,
    *,
    send_token: str,
    subject: str,
    recipient_email: str,
) -> dict[str, Any] | None:
    try:
        items = folder.Items
        try:
            items.Sort("[SentOn]", True)
        except Exception:
            try:
                items.Sort("[CreationTime]", True)
            except Exception:
                pass
        count = min(int(items.Count), SENT_ITEMS_SCAN_LIMIT)
    except Exception:
        return None

    for index in range(1, count + 1):
        item = None
        try:
            item = items.Item(index)
            if not _item_matches_send(
                item,
                send_token=send_token,
                subject=subject,
                recipient_email=recipient_email,
            ):
                continue
            return {
                "entry_id": str(getattr(item, "EntryID", "") or ""),
                "subject": str(getattr(item, "Subject", "") or ""),
                "sent": bool(getattr(item, "Sent", False)),
                "send_token": _item_send_token(item),
            }
        except Exception:
            continue
        finally:
            item = None
    return None


def _sent_items_wait_seconds() -> float:
    raw = os.getenv(
        "SMART_OFFICE_OUTLOOK_SENT_ITEMS_WAIT_SECONDS",
        str(DEFAULT_SENT_ITEMS_WAIT_SECONDS),
    )
    try:
        return max(10.0, min(120.0, float(raw)))
    except ValueError:
        return DEFAULT_SENT_ITEMS_WAIT_SECONDS


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

    outlook = None
    namespace = None
    sender_account = None
    mail = None
    verified_mail = None
    sent_folder = None
    outbox_folder = None
    outlook_explorer = None
    send_invoked = False
    draft_notice_removed = False
    outbox_observed = False
    outlook_window_ensured = False

    pythoncom.CoInitialize()
    try:
        stage = "outlook_connection"
        outlook, connection_mode = _outlook_application(win32com.client)
        namespace = outlook.GetNamespace("MAPI")

        stage = "sender_account_lookup"
        sender_account, detected_accounts = _find_sender_account(namespace, sender_email)
        sent_folder = _account_default_folder(
            namespace,
            sender_account,
            OUTLOOK_SENT_MAIL_FOLDER,
        )
        outbox_folder = _account_default_folder(
            namespace,
            sender_account,
            OUTLOOK_OUTBOX_FOLDER,
        )

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
        draft_notice_removed = True

        stage = "send_correlation"
        send_token = uuid.uuid4().hex
        _set_send_token(verified_mail, send_token)
        try:
            verified_mail.SaveSentMessageFolder = sent_folder
        except Exception:
            LOGGER.warning(
                "Outlook did not accept an explicit SaveSentMessageFolder; the account default will be used.",
                exc_info=True,
            )
        verified_mail.Save()

        stage = "outlook_window_persistence"
        outlook_explorer, outlook_window_ensured = _ensure_visible_outlook_explorer(
            outlook,
            namespace,
            sender_account,
        )

        stage = "send_invocation"
        verified_mail.SendUsingAccount = sender_account
        verified_mail.Send()
        send_invoked = True

        stage = "sent_items_verification"
        sent_match: dict[str, Any] | None = None
        deadline = time.monotonic() + _sent_items_wait_seconds()
        while time.monotonic() < deadline:
            sent_match = _find_matching_item(
                sent_folder,
                send_token=send_token,
                subject=observed_subject,
                recipient_email=recipient_email,
            )
            if sent_match is not None:
                break

            outbox_match = _find_matching_item(
                outbox_folder,
                send_token=send_token,
                subject=observed_subject,
                recipient_email=recipient_email,
            )
            if outbox_match is not None:
                outbox_observed = True
            time.sleep(SENT_ITEMS_POLL_SECONDS)

        if sent_match is None:
            if outbox_observed:
                raise RuntimeError(
                    "Outlook queued the approved message in Outbox but did not move it to "
                    "Sent Items before the verification timeout. Outlook has been kept open; "
                    "check network connectivity and the Outlook Outbox before retrying."
                )
            raise RuntimeError(
                "Outlook Send() returned, but no matching message appeared in the sender "
                "account's Sent Items before the verification timeout. Outlook has been kept "
                "open; inspect Outbox and account connectivity before retrying."
            )

        acceptance_evidence = "matching_item_in_sender_sent_items"
        sent_item_entry_id = str(sent_match.get("entry_id") or "")
        LOGGER.info(
            "OUTLOOK_SEND_SUCCESS sender=%s recipient_key=%s recipient=%s source_entry_id=%s sent_entry_id=%s connection_mode=%s detected_accounts=%s notice_removed=%s acceptance_evidence=%s outbox_observed=%s outlook_window_ensured=%s",
            sender_email,
            recipient.key,
            recipient_email,
            entry_id,
            sent_item_entry_id or "none",
            connection_mode,
            detected_accounts,
            True,
            acceptance_evidence,
            outbox_observed,
            outlook_window_ensured,
        )
        return ToolResult(
            tool_name="outlook_send_approved_draft",
            ok=True,
            message=(
                f"Outlook confirmed the approved email in Sent Items from {sender_email} to "
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
                "sent_item_entry_id": sent_item_entry_id or None,
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
                "outbox_observed": outbox_observed,
                "outlook_window_ensured": outlook_window_ensured,
                "sent": True,
                "delivery_confirmed": False,
            },
            raw={
                "source_outlook_draft_entry_id": entry_id,
                "sent_item_entry_id": sent_item_entry_id or None,
                "recipient_key": recipient.key,
                "approval_gated_email_send_enabled": True,
                "unrestricted_email_send_enabled": False,
                "draft_notice_removed": True,
                "send_invoked": True,
                "send_acceptance_evidence": acceptance_evidence,
                "outbox_observed": outbox_observed,
                "outlook_window_ensured": outlook_window_ensured,
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
            draft_notice_removed=draft_notice_removed,
            send_invoked=send_invoked,
            outbox_observed=outbox_observed,
            outlook_window_ensured=outlook_window_ensured,
        )
    finally:
        # Release COM proxies before ending the apartment. The visible Explorer window,
        # not a leaked Python reference, keeps Outlook running for transport completion.
        verified_mail = None
        mail = None
        sender_account = None
        sent_folder = None
        outbox_folder = None
        namespace = None
        outlook_explorer = None
        outlook = None
        gc.collect()
        pythoncom.CoUninitialize()
