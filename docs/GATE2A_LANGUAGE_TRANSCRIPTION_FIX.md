# Gate 2A spoken-language preservation fix

The previous language guard operated after speech recognition. It could keep an English backend answer in English, but it could not repair a transcript that GPT Realtime had already translated into Chinese.

The Realtime speech-understanding prompt now treats the spoken audio as authoritative:

- detect the language from the audio itself;
- transcribe each segment in the same spoken language;
- never translate English speech into Chinese;
- never translate Chinese speech into English;
- preserve genuine code-switching;
- treat the UI language selector only as an acoustic hint;
- preserve correction handling in both Chinese and English.

The patch is installed through `safeRealtimeAgentRuntime.ts`, which is loaded before the voice panel from `main.tsx`.
