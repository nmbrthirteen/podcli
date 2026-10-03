def normalize_engine(name: str | None) -> str:
    value = (name or "whisper-py").strip().lower()
    if value in ("whispercpp", "whisper-cpp", "whisper.cpp", "cpp"):
        return "whispercpp"
    if value in ("assemblyai", "assembly-ai", "aai"):
        return "assemblyai"
    if value in ("omnilingual", "omni", "omnilingual-asr"):
        return "omnilingual"
    return "whisper-py"


def is_assemblyai_engine(name: str | None) -> bool:
    return normalize_engine(name) == "assemblyai"


def is_omnilingual_engine(name: str | None) -> bool:
    return normalize_engine(name) == "omnilingual"
