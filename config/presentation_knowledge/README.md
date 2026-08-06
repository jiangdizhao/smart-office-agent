# Guided presentation script

Place the exhibition narration and technical Q&A DOCX in this directory.

Preferred filename:

`MEGA_SMART_展览讲解稿与技术答疑知识库.docx`

At runtime, the Backend reads the DOCX directly. The document must contain sections for PPT slides 2, 3 and 4, and each section must retain these markers:

- `Agent 简短讲解稿`
- `技术答疑稿`

The configured PowerPoint remains the existing default presentation; no PPT file is required in this directory.

Optional Backend environment variables:

- `SMART_OFFICE_PRESENTATION_SCRIPT_DIR`: override this directory.
- `SMART_OFFICE_PRESENTATION_QA_MODEL`: override the grounded current-slide Q&A model.
