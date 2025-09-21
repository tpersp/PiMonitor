from pathlib import Path
text = Path('pimonitor_api.py').read_text()
start = text.index("with open(WPA_SUPPLICANT_CONF, 'a'")
segment = text[start:start+120]
print(repr(segment))
