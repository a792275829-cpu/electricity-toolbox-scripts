"""Log redaction shared by GUI and headless processes."""
import re

def redact(text: str) -> str:
    text = re.sub(r'(?im)(authorization|cookie|set-cookie)\s*[:=][^\n]+', r'\1: [已隐藏]', text)
    text = re.sub(r'''(?ix)(["']?(?:password|passwd|username|access_token|refresh_token|token|secret|密码|账号)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&}\n]+)''', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)(--(?:password|username|token|secret)\s+)(?:"[^"]*"|\x27[^\x27]*\x27|\S+)', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)(https?://)[^/@\s]+:[^/@\s]+@', r'\1[已隐藏]@', text)
    text = re.sub(r'https?://[^\s<>"\']+', lambda m: re.sub(r'\?[^\s]*', '?[参数已隐藏]', m[0]), text)
    return text
