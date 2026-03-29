# -*- coding: UTF-8 -*-
import addonHandler
import globalPluginHandler
import scriptHandler
import gui
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from gui import settingsDialogs
import config
import ui
import webbrowser
import threading
import urllib.parse
import urllib.request
import json
import re
import html
import shutil
import subprocess
import nvwave
import os
import tempfile


# ---- Simple media playback (Windows MCI) ----
# NVDA's bundled wxPython may not include wx.media on some builds.
# To support basic play/pause/stop without extra dependencies, we use Windows MCI via ctypes.
try:
    import ctypes
    _MCI_ALIAS = "wikiFusionMCI"
    _mciSendStringW = ctypes.windll.winmm.mciSendStringW  # type: ignore[attr-defined]
    _mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    _mciSendStringW.restype = ctypes.c_uint

    def _mciSend(cmd: str) -> None:
        # MCI returns 0 on success. We intentionally keep this silent and handle failure by exception.
        err = _mciSendStringW(cmd, None, 0, None)
        if err != 0:
            raise OSError(int(err))


    def _mciQuery(cmd: str) -> str:
        # Query an MCI string result.
        buf = ctypes.create_unicode_buffer(256)
        err = _mciSendStringW(cmd, buf, 256, None)
        if err != 0:
            raise OSError(int(err))
        return (buf.value or "").strip()

    def _mciGetMode() -> str:
        # Returns playing/paused/stopped/not ready, etc.
        return _mciQuery(f"status {_MCI_ALIAS} mode")

    def _mciStopAndClose() -> None:
        try:
            _mciSend(f"stop {_MCI_ALIAS}")
        except Exception:
            pass
        try:
            _mciSend(f"close {_MCI_ALIAS}")
        except Exception:
            pass

    def _mciPlayPath(path: str) -> None:
        # Use explicit type when possible. MCI is most reliable for WAV/MP3.
        _mciStopAndClose()
        ext = os.path.splitext(path)[1].lower()
        mciType = ""
        if ext == ".mp3":
            mciType = " type mpegvideo"
        elif ext == ".wav":
            mciType = " type waveaudio"
        _mciSend(f"open \"{path}\"{mciType} alias {_MCI_ALIAS}")
        _mciSend(f"play {_MCI_ALIAS} from 0")

    def _mciPause() -> None:
        _mciSend(f"pause {_MCI_ALIAS}")

    def _mciResume() -> None:
        _mciSend(f"resume {_MCI_ALIAS}")

except Exception:
    _MCI_ALIAS = None  # type: ignore[assignment]

    def _mciStopAndClose() -> None:
        return

    def _mciPlayPath(path: str) -> None:
        raise OSError("MCI unavailable")

    def _mciPause() -> None:
        raise OSError("MCI unavailable")

    def _mciResume() -> None:
        raise OSError("MCI unavailable")

addonHandler.initTranslation()


def _whichFfmpeg():
    """Return ffmpeg executable path if available on PATH, else None."""
    try:
        return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    except Exception:
        return None


def _isFfmpegDecodableExt(ext):
    ext = (ext or "").lower()
    return ext in (".ogg", ".oga", ".opus", ".webm", ".m4a", ".aac", ".flac")


def _ffmpegDecodeToWav(ffmpegPath, srcPath, dstWavPath):
    """Decode srcPath to a PCM WAV at dstWavPath using ffmpeg."""
    cmd = [
        ffmpegPath,
        "-y",
        "-v", "error",
        "-i", srcPath,
        "-acodec", "pcm_s16le",
        "-ac", "2",
        "-ar", "44100",
        dstWavPath,
    ]
    subprocess.run(
        cmd,
        check=True,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

ADDON_NAME = "wikiFusion"

# ---- Config ----
_CONFIG_SPEC = {
    "soundsEnabled": "boolean(default=True)",
    "siteLangCode": "string(default=en)",
    "wpLangCode": "string(default=en)",
    "enableUncyclopedia": "boolean(default=False)",
    "enableUrbanDictionary": "boolean(default=False)",
    "preferredLanguageSection": "string(default=English)",
    "allowAnyLanguageFallback": "boolean(default=False)",
    "autoOpenInBrowser": "boolean(default=False)",
    "maxMatches": "integer(default=50, min=1, max=50)",
    "maxDefinitions": "integer(default=50, min=1, max=50)",
}

def _ensureConfig():
    # Ensure config spec exists
    try:
        spec = config.conf.spec
        if ADDON_NAME not in spec:
            spec[ADDON_NAME] = {}
        for k, v in _CONFIG_SPEC.items():
            spec[ADDON_NAME][k] = v
    except Exception:
        # If spec isn't available for some reason, we still try to proceed with defaults.
        pass
    # Ensure section exists
    if ADDON_NAME not in config.conf:
        config.conf[ADDON_NAME] = {}
    # Touch defaults by reading them
    s = config.conf[ADDON_NAME]
    for k in _CONFIG_SPEC.keys():
        s.get(k)

def _s():
    _ensureConfig()
    return config.conf[ADDON_NAME]

def _coerce_bool(val, default=False):
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off", ""):
            return False
    return default

def _getInt(val, default, minv=None, maxv=None):
    try:
        i = int(val)
    except Exception:
        i = default
    if minv is not None:
        i = max(minv, i)
    if maxv is not None:
        i = min(maxv, i)
    return i

def _addonPath(*parts):
    try:
        a = addonHandler.getCodeAddon()
        base = a.path
    except Exception:
        base = os.path.dirname(__file__)
    return os.path.join(base, *parts)

def _playSound(filename):
    try:
        if not _coerce_bool(_s().get("soundsEnabled", True), True):
            return
        path = _addonPath("sounds", filename)
        path = os.path.normpath(path)
        if os.path.isfile(path):
            nvwave.playWaveFile(path)
    except Exception:
        pass

# ---- Wiktionary helpers ----
def _apiBase():
    site = str(_s().get("siteLangCode", "en") or "en").strip()
    # basic hardening
    site = re.sub(r"[^a-zA-Z0-9\-]", "", site) or "en"
    return f"https://{site}.wiktionary.org/w/api.php"

def _entryUrl(title):
    site = str(_s().get("siteLangCode", "en") or "en").strip()
    site = re.sub(r"[^a-zA-Z0-9\-]", "", site) or "en"
    return f"https://{site}.wiktionary.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


# ---- Wikipedia helpers ----
def _wpApiBase():
    site = str(_s().get("wpLangCode", "en") or "en").strip()
    site = re.sub(r"[^a-zA-Z0-9\-]", "", site) or "en"
    return f"https://{site}.wikipedia.org/w/api.php"

def _wpEntryUrl(title):
    site = str(_s().get("wpLangCode", "en") or "en").strip()
    site = re.sub(r"[^a-zA-Z0-9\-]", "", site) or "en"
    return f"https://{site}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


# ---- Optional sources ----
def _uncyclopediaApiBase():
    return "https://en.uncyclopedia.co/w/api.php"

def _uncyclopediaEntryUrl(title):
    return f"https://en.uncyclopedia.co/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"

def _urbanDictionaryEntryUrl(title, defid=None):
    params = {"term": title}
    if defid:
        params["defid"] = str(defid)
    return "https://www.urbandictionary.com/define.php?" + urllib.parse.urlencode(params)

def _wpOpensearch(q):
    """
    Wikipedia opensearch. Returns list of titles.
    """
    base = _wpApiBase()
    params = {
        "action": "opensearch",
        "search": q,
        "limit": _getInt(_s().get("maxMatches", 50), 50, 1, 50),
        "namespace": 0,
        "format": "json",
    }
    url = base + "?" + urllib.parse.urlencode(params)
    data = _httpGetJson(url)
    # opensearch format: [searchterm, titles[], descriptions[], urls[]]
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
        return [str(x) for x in data[1] if x]
    return []

def _wpSummary(title):
    """
    Returns plaintext summary for a Wikipedia article.
    Uses MediaWiki extracts via action=query to avoid REST endpoint variability.
    """
    base = _wpApiBase()
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": 1,
        "exsectionformat": "plain",
        "redirects": 1,
        "titles": title,
        "format": "json",
    }
    url = base + "?" + urllib.parse.urlencode(params)
    data = _httpGetJson(url)
    pages = (data or {}).get("query", {}).get("pages", {})
    if not isinstance(pages, dict) or not pages:
        return ""
    # pages is dict keyed by pageid
    page = next(iter(pages.values()))
    if isinstance(page, dict):
        return str(page.get("extract") or "")
    return ""

def _mwOpenSearch(apiBase, query):
    params = {
        "action": "opensearch",
        "search": query,
        "limit": _getInt(_s().get("maxMatches", 50), 50, 1, 50),
        "namespace": 0,
        "format": "json",
    }
    url = apiBase + "?" + urllib.parse.urlencode(params)
    data = _httpGetJson(url)
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
        return [str(x) for x in data[1] if x]
    return []

def _mwSummary(apiBase, title):
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": 1,
        "exsectionformat": "plain",
        "redirects": 1,
        "titles": title,
        "format": "json",
    }
    url = apiBase + "?" + urllib.parse.urlencode(params)
    data = _httpGetJson(url)
    pages = (data or {}).get("query", {}).get("pages", {})
    if not isinstance(pages, dict) or not pages:
        return ""
    page = next(iter(pages.values()))
    if isinstance(page, dict):
        return str(page.get("extract") or "")
    return ""

def _uncyclopediaOpensearch(q):
    return _mwOpenSearch(_uncyclopediaApiBase(), q)

def _uncyclopediaSummary(title):
    return _mwSummary(_uncyclopediaApiBase(), title)

def _httpGetJson(url, params=None, timeout=10):
    """HTTP GET and decode JSON.

    Backward compatible:
      - _httpGetJson(url) -> uses default timeout
      - _httpGetJson(url, 15) -> timeout=15
      - _httpGetJson(baseUrl, paramsDict) -> appends query string built from params
      - _httpGetJson(baseUrl, paramsDict, 15) -> params + timeout
    """
    # Back-compat: if second arg is numeric, treat it as timeout.
    if params is not None and not isinstance(params, dict):
        try:
            timeout = int(params)
            params = None
        except Exception:
            # If it's not an int, fall through and let urllib raise.
            pass

    try:
        if isinstance(params, dict) and params:
            try:
                qs = urllib.parse.urlencode(params)
            except Exception:
                qs = ""
            if qs:
                url = url + ("&" if "?" in url else "?") + qs
    except Exception:
        pass

    req = urllib.request.Request(url, headers={"User-Agent": "wikiFusion (NVDA addon)"})
    with urllib.request.urlopen(req, timeout=int(timeout)) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8", errors="replace"))

def _httpGetText(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "wikiFusion (NVDA addon)"})
    with urllib.request.urlopen(req, timeout=int(timeout)) as resp:
        data = resp.read()
    return data.decode("utf-8", errors="replace")



_MEDIA_EXTS = (".ogg", ".oga", ".wav", ".mp3", ".flac", ".m4a", ".aac")

def _batched(items, batchSize):
    for i in range(0, len(items), batchSize):
        yield items[i:i + batchSize]

def _fileTitlesToUrls(apiBase, fileTitles):
    # Returns list of (fileTitle, url) for titles that resolve to a URL.
    if not fileTitles:
        return []
    results = []
    for batch in _batched(fileTitles, 20):
        params = {
            "action": "query",
            "titles": "|".join(batch),
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        }
        data = _httpGetJson(apiBase, params)
        pages = (data or {}).get("query", {}).get("pages", {}) or {}
        for page in pages.values():
            title = str(page.get("title", "") or "")
            ii = page.get("imageinfo", []) or []
            if ii and isinstance(ii, list):
                url = str(ii[0].get("url", "") or "")
                if title and url:
                    results.append((title, url))
    return results

def _extractMediaFromParseImages(images):
    out = []
    for name in (images or []):
        fn = str(name or "")
        if not fn:
            continue
        lower = fn.lower()
        if lower.endswith(_MEDIA_EXTS):
            if not fn.lower().startswith("file:"):
                fn = "File:" + fn
            out.append(fn)
    # stable order, de-dup
    seen = set()
    deduped = []
    for t in out:
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(t)
    return deduped

def _wpMediaFiles(title, limit=50):
    # Uses Wikipedia API to find media files referenced by the page (commonly pronunciation OGG).
    apiBase = _wpApiBase()
    params = {"action": "parse", "page": title, "prop": "images", "format": "json"}
    data = _httpGetJson(apiBase, params)
    images = (data or {}).get("parse", {}).get("images", []) or []
    fileTitles = _extractMediaFromParseImages(images)[:int(limit)]
    pairs = _fileTitlesToUrls(apiBase, fileTitles)
    items = []
    for fileTitle, url in pairs:
        label = fileTitle.replace("File:", "").strip()
        items.append({"title": fileTitle, "label": label, "url": url})
    return items

def _uncyclopediaMediaFiles(title, limit=50):
    apiBase = _uncyclopediaApiBase()
    params = {"action": "parse", "page": title, "prop": "images", "format": "json"}
    data = _httpGetJson(apiBase, params)
    images = (data or {}).get("parse", {}).get("images", []) or []
    fileTitles = _extractMediaFromParseImages(images)[:int(limit)]
    pairs = _fileTitlesToUrls(apiBase, fileTitles)
    items = []
    for fileTitle, url in pairs:
        label = fileTitle.replace("File:", "").strip()
        items.append({"title": fileTitle, "label": label, "url": url})
    return items

def _wtMediaFiles(title, limit=50):
    # Wiktionary can also contain pronunciation audio.
    apiBase = _apiBase()
    params = {"action": "parse", "page": title, "prop": "images", "format": "json"}
    data = _httpGetJson(apiBase, params)
    images = (data or {}).get("parse", {}).get("images", []) or []
    fileTitles = _extractMediaFromParseImages(images)[:int(limit)]
    pairs = _fileTitlesToUrls(apiBase, fileTitles)
    items = []
    for fileTitle, url in pairs:
        label = fileTitle.replace("File:", "").strip()
        items.append({"title": fileTitle, "label": label, "url": url})
    return items
def _opensearch(query):
    maxMatches = _getInt(_s().get("maxMatches", 50), 50, 1, 50)
    params = {
        "action": "opensearch",
        "search": query,
        "limit": str(maxMatches),
        "namespace": "0",
        "format": "json",
    }
    url = _apiBase() + "?" + urllib.parse.urlencode(params)
    j = _httpGetJson(url)
    # [searchTerm, titles[], descriptions[], urls[]]
    titles = j[1] if len(j) > 1 else []
    return [t for t in titles if isinstance(t, str)]

def _urbanDictionaryAutocomplete(query):
    params = {"term": query}
    data = _httpGetJson("https://api.urbandictionary.com/v0/autocomplete", params)
    if isinstance(data, list):
        return [str(x) for x in data if x]
    return []

def _cleanUrbanText(text):
    text = html.unescape(str(text or ""))
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    return re.sub(r"\r\n?", "\n", text).strip()

def _urbanDictionaryDefinitions(term):
    data = _httpGetJson("https://api.urbandictionary.com/v0/define", {"term": term})
    entries = (data or {}).get("list", []) or []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        out.append({
            "word": str(entry.get("word") or term),
            "definition": _cleanUrbanText(entry.get("definition") or ""),
            "example": _cleanUrbanText(entry.get("example") or ""),
            "author": str(entry.get("author") or ""),
            "thumbsUp": int(entry.get("thumbs_up") or 0),
            "thumbsDown": int(entry.get("thumbs_down") or 0),
            "writtenOn": str(entry.get("written_on") or ""),
            "url": str(entry.get("permalink") or _urbanDictionaryEntryUrl(term, entry.get("defid"))),
        })
    return out

def _enabledOptionalSources():
    s = _s()
    enabled = []
    if _coerce_bool(s.get("enableUncyclopedia", False), False):
        enabled.append("Uncyclopedia")
    if _coerce_bool(s.get("enableUrbanDictionary", False), False):
        enabled.append("Urban Dictionary")
    return enabled

def _displaySourceOrder():
    base = ["Wikipedia", "Wiktionary"]
    extras = _enabledOptionalSources()
    if "Uncyclopedia" in extras:
        base.append("Uncyclopedia")
    if "Urban Dictionary" in extras:
        base.append("Urban Dictionary")
    return base

def _prioritySourceOrder(single):
    base = ["Wiktionary", "Wikipedia"] if single else ["Wikipedia", "Wiktionary"]
    extras = _enabledOptionalSources()
    if single:
        if "Urban Dictionary" in extras:
            base.insert(1, "Urban Dictionary")
        if "Uncyclopedia" in extras:
            base.append("Uncyclopedia")
    else:
        if "Uncyclopedia" in extras:
            base.insert(1, "Uncyclopedia")
        if "Urban Dictionary" in extras:
            base.append("Urban Dictionary")
    return base

def _sourceEntryUrl(source, title):
    if source == "Wikipedia":
        return _wpEntryUrl(title)
    if source == "Wiktionary":
        return _entryUrl(title)
    if source == "Uncyclopedia":
        return _uncyclopediaEntryUrl(title)
    if source == "Urban Dictionary":
        return _urbanDictionaryEntryUrl(title)
    return ""


def _isSingleWord(q):
    # Treat hyphens/apostrophes as part of a "word" for routing purposes.
    q = (q or "").strip()
    if not q:
        return False
    if len(q.split()) != 1:
        return False
    return True

def _superSearch(q):
    """
    Routing rules (display + exact-match priority):
    - Single word: Wiktionary is primary, Wikipedia is secondary.
    - Phrase (2+ words): Wikipedia is primary, Wiktionary is secondary.

    We *always* query both services (up to maxMatches each) and return a combined list
    ordered by primary first, then secondary. Each item is: {title, source}.
    """
    q = (q or "").strip()
    if not q:
        return []

    single = _isSingleWord(q)

    sourceTitles = {
        "Wiktionary": [],
        "Wikipedia": [],
        "Uncyclopedia": [],
        "Urban Dictionary": [],
    }
    try:
        sourceTitles["Wiktionary"] = _opensearch(q)
    except Exception:
        sourceTitles["Wiktionary"] = []
    try:
        sourceTitles["Wikipedia"] = _wpOpensearch(q)
    except Exception:
        sourceTitles["Wikipedia"] = []
    if _coerce_bool(_s().get("enableUncyclopedia", False), False):
        try:
            sourceTitles["Uncyclopedia"] = _uncyclopediaOpensearch(q)
        except Exception:
            sourceTitles["Uncyclopedia"] = []
    if _coerce_bool(_s().get("enableUrbanDictionary", False), False):
        try:
            sourceTitles["Urban Dictionary"] = _urbanDictionaryAutocomplete(q)
        except Exception:
            sourceTitles["Urban Dictionary"] = []

    # De-dup within each source (opensearch usually already unique, but be safe)
    def _dedup(items):
        out=[]
        seen=set()
        for it in items:
            key=(it.get("source"), (it.get("title") or "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
        return out

    ordered = []
    for source in _prioritySourceOrder(single):
        ordered.extend({"title": t, "source": source} for t in (sourceTitles.get(source) or []))

    return _dedup(ordered)




_LANG_SECTION_RE = re.compile(r"^==\s*([^=]+?)\s*==\s*$", re.M)

def _splitLanguageSections(wikitext):
    # Returns list of (langName, sectionText)
    matches = list(_LANG_SECTION_RE.finditer(wikitext))
    sections = []
    if not matches:
        return sections
    for idx, m in enumerate(matches):
        lang = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(wikitext)
        sections.append((lang, wikitext[start:end]))
    return sections

def _cleanLine(line):
    # Minimal clean: strip refs and formatting, and render templates into readable text.
    line = re.sub(r"<ref[^>]*>.*?</ref>", "", line, flags=re.I)
    line = re.sub(r"<ref[^/>]*/\s*>", "", line, flags=re.I)

    # Render simple (non-nested) templates like {{place|...}} into something readable
    # instead of deleting them (which can erase the entire definition for place/name entries).
    def _renderTemplate(m):
        inner = m.group(1)
        parts = [p.strip() for p in inner.split("|") if p.strip()]
        if not parts:
            return ""
        # drop the template name if it looks noisy, but keep it if it's meaningful text
        name = parts[0].strip()
        params = parts[1:]

        # drop obvious language codes (e.g. en, fr, nb, nn) when present as a bare param
        cleaned = []
        for p in params:
            # ignore named params keys; keep values
            if "=" in p:
                k, v = p.split("=", 1)
                p = v.strip()
            if re.fullmatch(r"[a-z]{2,3}(-[a-z]{2,4})?", p, flags=re.I):
                continue
            # simplify tokens like c/Norway, county/Nordland -> Norway, Nordland
            p = re.sub(r"\b[a-z]+/([A-Za-z].+?)\b", r"\1", p)
            cleaned.append(p)

        # If nothing left, fall back to template name (better than blank)
        textOut = ""
        if cleaned:
            textOut = " ".join(cleaned)
        else:
            textOut = name

        return textOut.strip()

    # Replace templates iteratively (handles multiple templates on one line)
    line = re.sub(r"\{\{([^{}]*?)\}\}", _renderTemplate, line)

    # Wikilinks
    line = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", line)
    # Bold/italic markup
    line = re.sub(r"''+", "", line)
    line = line.replace("&nbsp;", " ")
    line = re.sub(r"\s+", " ", line).strip()
    return line




def _extractDefinitionsFromSection(sectionText, maxDefs):
    defs = []
    for raw in sectionText.splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        # Skip examples/quotes lines which are usually "#:" or "#*"
        if line.startswith("#:") or line.startswith("#*"):
            continue
        # Remove leading # markers (supports #, ##, ###) and whitespace
        line = re.sub(r"^#+\s*", "", line)
        t = _cleanLine(line)
        if not t:
            continue
        # avoid junk punctuation-only lines
        if re.fullmatch(r"[:.\-–—]+", t):
            continue
        defs.append(t)
        if len(defs) >= maxDefs:
            break
    return defs


# ---- Related terms helpers (Synonyms / Antonyms) ----
_HEADING_RE = re.compile(r"^(?P<eq>={3,6})\s*(?P<title>[^=]+?)\s*(?P=eq)\s*$", re.M)

def _iterHeadings(text):
    # yields (title, level, startContentIndex, endContentIndex)
    ms = list(_HEADING_RE.finditer(text or ""))
    for idx, m in enumerate(ms):
        title = (m.group("title") or "").strip()
        level = len(m.group("eq") or "")
        start = m.end()
        end = ms[idx + 1].start() if idx + 1 < len(ms) else len(text)
        yield (title, level, start, end)

def _extractRelatedList(sectionText, headingName, maxItems=12):
    # Find any subsection titled headingName (case-insensitive) and pull list items from it.
    if not sectionText:
        return []
    out = []
    seen = set()

    for title, level, start, end in _iterHeadings(sectionText):
        if title.strip().lower() != headingName.strip().lower():
            continue

        block = sectionText[start:end]
        for raw in block.splitlines():
            line = raw.strip()
            if not line:
                continue
            # list items are typically '* ...' or sometimes ':* ...'
            line = re.sub(r"^:+\s*", "", line)
            if not line.startswith("*"):
                continue
            line = re.sub(r"^\*+\s*", "", line)
            cleaned = _cleanLine(line)
            if not cleaned:
                continue

            # Split common list separators, keep readable tokens
            parts = re.split(r"\s*(?:,|;|/|\u2022|\u00b7)\s*", cleaned)
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                # Avoid long glosses like "word (sense 1)"? Keep parentheses but strip leading/trailing punctuation
                p = p.strip(" -–—•·")
                key = p.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
                if len(out) >= maxItems:
                    return out
    return out

def _fetchWikitext(title):
    params = {
        "action": "parse",
        "page": title,
        "prop": "wikitext",
        "format": "json",
    }
    url = _apiBase() + "?" + urllib.parse.urlencode(params)
    j = _httpGetJson(url)
    wt = ""
    try:
        wt = j["parse"]["wikitext"]["*"]
    except Exception:
        wt = ""
    return wt

def _lookupDefinitions(title):
    maxDefs = _getInt(_s().get("maxDefinitions", 50), 50, 1, 50)
    preferred = str(_s().get("preferredLanguageSection", "English") or "English").strip()
    allowAny = _coerce_bool(_s().get("allowAnyLanguageFallback", False), False)

    wt = _fetchWikitext(title)
    if not wt:
        return (None, [], "Unknown", [], [])

    sections = _splitLanguageSections(wt)
    if not sections:
        # Some pages are single-language without header; try whole text
        defs = _extractDefinitionsFromSection(wt, maxDefs)
        syns = _extractRelatedList(wt, "Synonyms")
        ants = _extractRelatedList(wt, "Antonyms")
        return ("Unknown", defs, "Unknown", syns, ants)

    # Try preferred first
    for lang, txt in sections:
        if lang.lower() == preferred.lower():
            defs = _extractDefinitionsFromSection(txt, maxDefs)
            syns = _extractRelatedList(txt, "Synonyms")
            ants = _extractRelatedList(txt, "Antonyms")
            if defs or syns or ants:
                return (lang, defs, lang, syns, ants)
            break

    if allowAny:
        for lang, txt in sections:
            defs = _extractDefinitionsFromSection(txt, maxDefs)
            syns = _extractRelatedList(txt, "Synonyms")
            ants = _extractRelatedList(txt, "Antonyms")
            if defs or syns or ants:
                return (lang, defs, lang, syns, ants)

    # Nothing useful found in preferred (or any allowed fallback)
    return (preferred, [], preferred, [], [])


# ---- Settings Panel ----# ---- Settings Panel ----
class WikiFusionSettingsPanel(SettingsPanel):
    title = _("Wiki Fusion")

    def makeSettings(self, sizer):
        _ensureConfig()
        s = _s()
        helper = guiHelper.BoxSizerHelper(self, sizer=sizer)

        self.soundsChk = helper.addItem(wx.CheckBox(self, label=_("Enable sounds")))
        self.soundsChk.SetValue(_coerce_bool(s.get("soundsEnabled", True), True))

        self.siteLang = helper.addLabeledControl(_("Wiktionary site language code (e.g. en, fr, pt-br)"), wx.TextCtrl)
        self.siteLang.SetValue(str(s.get("siteLangCode", "en") or "en"))

        self.wpLang = helper.addLabeledControl(_("Wikipedia site language code (e.g. en, sv, es)"), wx.TextCtrl)
        self.wpLang.SetValue(str(s.get("wpLangCode", "en") or "en"))

        self.uncyclopediaChk = helper.addItem(wx.CheckBox(self, label=_("Include Uncyclopedia results")))
        self.uncyclopediaChk.SetValue(_coerce_bool(s.get("enableUncyclopedia", False), False))

        self.urbanDictionaryChk = helper.addItem(wx.CheckBox(self, label=_("Include Urban Dictionary results")))
        self.urbanDictionaryChk.SetValue(_coerce_bool(s.get("enableUrbanDictionary", False), False))

        self.prefLang = helper.addLabeledControl(_("Preferred language section name (e.g. English)"), wx.TextCtrl)
        self.prefLang.SetValue(str(s.get("preferredLanguageSection", "English") or "English"))

        self.anyLangChk = helper.addItem(wx.CheckBox(self, label=_("Allow any language fallback if preferred language has no definitions")))
        self.anyLangChk.SetValue(_coerce_bool(s.get("allowAnyLanguageFallback", False), False))

        self.autoOpenChk = helper.addItem(wx.CheckBox(self, label=_("Automatically open entry in browser after successful lookup")))
        self.autoOpenChk.SetValue(_coerce_bool(s.get("autoOpenInBrowser", False), False))

        self.maxMatchesSpin = helper.addLabeledControl(_("Maximum matches to display"), wx.SpinCtrl, min=1, max=50)
        self.maxMatchesSpin.SetValue(_getInt(s.get("maxMatches", 50), 50, 1, 50))

        self.maxDefsSpin = helper.addLabeledControl(_("Maximum definitions to display"), wx.SpinCtrl, min=1, max=50)
        self.maxDefsSpin.SetValue(_getInt(s.get("maxDefinitions", 50), 50, 1, 50))

    def onSave(self):
        s = _s()
        s["soundsEnabled"] = bool(self.soundsChk.GetValue())
        s["siteLangCode"] = self.siteLang.GetValue().strip() or "en"
        s["wpLangCode"] = self.wpLang.GetValue().strip() or "en"
        s["enableUncyclopedia"] = bool(self.uncyclopediaChk.GetValue())
        s["enableUrbanDictionary"] = bool(self.urbanDictionaryChk.GetValue())
        s["preferredLanguageSection"] = self.prefLang.GetValue().strip() or "English"
        s["allowAnyLanguageFallback"] = bool(self.anyLangChk.GetValue())
        s["autoOpenInBrowser"] = bool(self.autoOpenChk.GetValue())
        s["maxMatches"] = int(self.maxMatchesSpin.GetValue())
        s["maxDefinitions"] = int(self.maxDefsSpin.GetValue())

# register panel
def _registerSettings():
    try:
        settingsDialogs.NVDASettingsDialog.categoryClasses.append(WikiFusionSettingsPanel)
    except Exception:
        pass

_registerSettings()

# ---- Main dialog ----
class WikiFusionDialog(wx.Dialog):
    def __init__(self, parent):
        super(WikiFusionDialog, self).__init__(parent, title=_("Wiki Fusion"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        _ensureConfig()

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        # Search row
        searchSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.query = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        searchSizer.Add(self.query, 1, wx.EXPAND | wx.ALL, 5)
        self.searchBtn = wx.Button(self, label=_("&Search"))
        searchSizer.Add(self.searchBtn, 0, wx.ALL, 5)
        mainSizer.Add(searchSizer, 0, wx.EXPAND)

        # Results (Tree: source buckets)
        self.results = wx.TreeCtrl(
            self,
            style=wx.TR_HIDE_ROOT | wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_SINGLE | wx.BORDER_SUNKEN
        )
        mainSizer.Add(self.results, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # Article + Media
        contentSizer = wx.BoxSizer(wx.HORIZONTAL)

        self.article = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SUNKEN)
        contentSizer.Add(self.article, 3, wx.EXPAND | wx.ALL, 5)

        self.mediaPanel = wx.Panel(self)
        mediaSizer = wx.BoxSizer(wx.VERTICAL)
        mediaSizer.Add(wx.StaticText(self.mediaPanel, label=_("Media")), 0, wx.LEFT | wx.TOP, 5)
        self.mediaList = wx.ListBox(self.mediaPanel, choices=[], style=wx.LB_SINGLE | wx.BORDER_SUNKEN)
        mediaSizer.Add(self.mediaList, 1, wx.EXPAND | wx.ALL, 5)

        mediaBtnSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.mediaPlayBtn = wx.Button(self.mediaPanel, label=_("&Play/Stop"))
        self.mediaPauseBtn = wx.Button(self.mediaPanel, label=_("P&ause/Resume"))
        self.mediaDownloadBtn = wx.Button(self.mediaPanel, label=_("&Download"))
        mediaBtnSizer.Add(self.mediaPlayBtn, 1, wx.EXPAND | wx.ALL, 2)
        mediaBtnSizer.Add(self.mediaPauseBtn, 1, wx.EXPAND | wx.ALL, 2)
        mediaBtnSizer.Add(self.mediaDownloadBtn, 1, wx.EXPAND | wx.ALL, 2)
        mediaSizer.Add(mediaBtnSizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)
        self.mediaPanel.SetSizer(mediaSizer)
        self.mediaPanel.Hide()
        contentSizer.Add(self.mediaPanel, 1, wx.EXPAND)

        mainSizer.Add(contentSizer, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 0)

        # Media playback backend (optional)
        self._mediaItems = []
        self._mediaPlayingPath = None
        self._mediaPaused = False
        self._mediaTempDir = os.path.join(tempfile.gettempdir(), "wikiFusionMedia")
        try:
            os.makedirs(self._mediaTempDir, exist_ok=True)
        except Exception:
            pass

        try:
            import wx.media as wxmedia  # type: ignore
        except Exception:
            wxmedia = None

        self._wxmedia = wxmedia
        self._mediaCtrl = None
        if wxmedia is not None:
            try:
                self._mediaCtrl = wxmedia.MediaCtrl(self)
                self._mediaCtrl.Hide()
                try:
                    if hasattr(wxmedia, "EVT_MEDIA_FINISHED"):
                        self._mediaCtrl.Bind(wxmedia.EVT_MEDIA_FINISHED, self._onMediaFinished)
                except Exception:
                    pass
            except Exception:
                self._mediaCtrl = None
# Buttons
        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.openBtn = wx.Button(self, label=_("&Open in Browser"))
        self.clearBtn = wx.Button(self, label=_("C&lear"))
        self.closeBtn = wx.Button(self, label=_("&Close"))
        btnSizer.Add(self.openBtn, 0, wx.ALL, 5)
        btnSizer.Add(self.clearBtn, 0, wx.ALL, 5)
        btnSizer.AddStretchSpacer(1)
        btnSizer.Add(self.closeBtn, 0, wx.ALL, 5)
        mainSizer.Add(btnSizer, 0, wx.EXPAND)

        self.SetSizer(mainSizer)
        self.SetSize((720, 520))

        # Tree root + state
        self._root = self.results.AddRoot("root")
        self._treeDataByItem = {}     # treeItemId -> {title, source}
        self._treeItemByKey = {}      # (source, titleLower) -> treeItemId
        self._currentUrl = None
        self._currentItem = None

        # Bindings
        self.query.Bind(wx.EVT_TEXT_ENTER, self.onSearch)
        self.searchBtn.Bind(wx.EVT_BUTTON, self.onSearch)

        self.results.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.onActivateTreeItem)
        self.results.Bind(wx.EVT_KEY_DOWN, self.onResultsKeyDown)
        self.results.Bind(wx.EVT_CHAR_HOOK, self.onResultsCharHook)

        self.openBtn.Bind(wx.EVT_BUTTON, self.onOpenBrowser)
        self.clearBtn.Bind(wx.EVT_BUTTON, self.onClear)
        self.closeBtn.Bind(wx.EVT_BUTTON, self.onClose)

        # Media buttons
        self.mediaPlayBtn.Bind(wx.EVT_BUTTON, lambda evt: self._mediaPlaySelected())
        self.mediaPauseBtn.Bind(wx.EVT_BUTTON, lambda evt: self._mediaPauseToggle())
        self.mediaDownloadBtn.Bind(wx.EVT_BUTTON, lambda evt: self._mediaDownloadSelected())

        self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
        self.Bind(wx.EVT_CLOSE, self.onClose)

        self.openBtn.Enable(False)

        # Media list bindings
        self.mediaList.Bind(wx.EVT_KEY_DOWN, self.onMediaKeyDown)
        self.mediaList.Bind(wx.EVT_CHAR_HOOK, self.onMediaCharHook)
        self.mediaList.Bind(wx.EVT_LISTBOX_DCLICK, lambda evt: self._mediaPlaySelected())

        wx.CallAfter(self.query.SetFocus)

    def onCharHook(self, evt):
        key = evt.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.onClose(None)
            return
        if key == wx.WXK_F1:
            self._openHelp()
            return
        evt.Skip()

    def _openHelp(self):
        # Open the add-on help file (doc/en/readme.html) in the default browser.
        try:
            helpPath = _addonPath("doc", "en", "readme.html")
            if not helpPath or not os.path.isfile(helpPath):
                ui.message(_("Help file not found."))
                return
            if hasattr(os, "startfile"):
                os.startfile(helpPath)  # type: ignore[attr-defined]
            else:
                webbrowser.open("file://" + urllib.parse.quote(helpPath))
        except Exception as e:
            ui.message(_("Unable to open help: {0}").format(e))


    def _getSelectedMediaItem(self):
        if not getattr(self, "_mediaItems", None):
            return None
        idx = -1
        try:
            idx = self.mediaList.GetSelection()
        except Exception:
            idx = -1
        if idx is None or idx < 0 or idx >= len(self._mediaItems):
            return None
        return self._mediaItems[idx]

    def _mediaStop(self):
        try:
            _mciStopAndClose()
        except Exception:
            pass
        if getattr(self, "_mediaCtrl", None) is not None:
            try:
                self._mediaCtrl.Stop()
            except Exception:
                pass
        self._mediaPlayingPath = None
        self._mediaPaused = False

    def _onMediaFinished(self, evt):
        # Reset state when playback ends naturally, so Enter will play again immediately.
        self._mediaPlayingPath = None
        self._mediaPaused = False
        try:
            evt.Skip()
        except Exception:
            pass

    def _isMediaActive(self) -> bool:
        # True if the in-addon player is currently playing or paused.
        if getattr(self, "_mediaCtrl", None) is not None and getattr(self, "_wxmedia", None) is not None:
            try:
                st = self._mediaCtrl.GetState()
                playing = getattr(self._wxmedia, "MEDIASTATE_PLAYING", None)
                paused = getattr(self._wxmedia, "MEDIASTATE_PAUSED", None)
                if st == playing or st == paused:
                    return True
            except Exception:
                pass
        try:
            mode = (_mciGetMode() or "").strip().lower()
            if mode in ("playing", "paused"):
                return True
        except Exception:
            pass
        return False

    def _mediaPauseToggle(self):
        if self._mediaPlayingPath is None:
            return

        # Prefer wx.media if present.
        if getattr(self, "_mediaCtrl", None) is not None:
            try:
                if not self._mediaPaused:
                    self._mediaCtrl.Pause()
                    self._mediaPaused = True
                else:
                    self._mediaCtrl.Play()
                    self._mediaPaused = False
                return
            except Exception:
                pass

        # Fallback: Windows MCI pause/resume.
        try:
            if not self._mediaPaused:
                _mciPause()
                self._mediaPaused = True
            else:
                _mciResume()
                self._mediaPaused = False
        except Exception:
            pass

    def _downloadToPath(self, url, destPath):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "wikiFusion (NVDA add-on)",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        with open(destPath, "wb") as f:
            f.write(data)

    def _mediaPlaySelected(self):
        item = self._getSelectedMediaItem()
        if not item:
            return

        url = str(item.get("url", "") or "")
        label = str(item.get("label", "") or "media")
        if not url:
            return

        # Build a stable filename (keep extension if present in label or URL).
        urlPath = ""
        try:
            urlPath = urllib.parse.urlparse(url).path or ""
        except Exception:
            urlPath = ""
        urlName = os.path.basename(urlPath) if urlPath else ""
        labelName = label.strip() or "media"

        ext = os.path.splitext(labelName)[1]
        if not ext and urlName:
            ext = os.path.splitext(urlName)[1]
        if not ext:
            ext = ".bin"

        base = os.path.splitext(labelName)[0] or os.path.splitext(urlName)[0] or "media"
        safeBase = re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:120].strip("._-") or "media"
        safeName = safeBase + ext.lower()
        tempPath = os.path.join(self._mediaTempDir, safeName)

        # Toggle play/stop for the selected file.
        # If playback has already finished, we treat Enter as "play again" (no double-press).
        try:
            if self._mediaPlayingPath and (
                os.path.normcase(self._mediaPlayingPath) == os.path.normcase(tempPath)
                or os.path.normcase(self._mediaPlayingPath) == os.path.normcase(os.path.splitext(tempPath)[0] + ".wav")
            ):
                if self._isMediaActive():
                    self._mediaStop()
                    return
                # Finished/stopped already; reset and fall through to play again.
                self._mediaPlayingPath = None
                self._mediaPaused = False
        except Exception:
            pass


        # Download to temp (only if not already present).
        try:
            if not os.path.isfile(tempPath) or os.path.getsize(tempPath) == 0:
                self._downloadToPath(url, tempPath)
        except Exception as e:
            ui.message(_("Failed to download media: {0}").format(e))
            return

        
        # If this is a format Windows often can't decode natively (e.g. OGG/OPUS),
        # decode to WAV via ffmpeg (if available) so we can still play in-addon.
        playPath = tempPath
        try:
            if _isFfmpegDecodableExt(ext):
                ffmpegPath = _whichFfmpeg()
                if not ffmpegPath:
                    ui.message(_("This audio format needs ffmpeg for in-app playback. Install ffmpeg or use Ctrl+Enter to download."))
                    return
                wavPath = os.path.splitext(tempPath)[0] + ".wav"
                if not os.path.isfile(wavPath) or os.path.getsize(wavPath) == 0:
                    _ffmpegDecodeToWav(ffmpegPath, tempPath, wavPath)
                playPath = wavPath
        except Exception as e:
            ui.message(_("Failed to prepare audio for playback: {0}").format(e))
            return

# Play using wx.media if available; otherwise try Windows MCI; otherwise open externally.
        if getattr(self, "_mediaCtrl", None) is not None:
            try:
                if self._mediaCtrl.Load(playPath):
                    self._mediaCtrl.Play()
                    self._mediaPlayingPath = playPath
                    self._mediaPaused = False
                    return
            except Exception:
                pass

        try:
            _mciPlayPath(playPath)
            self._mediaPlayingPath = playPath
            self._mediaPaused = False
            return
        except Exception:
            pass

        ui.message(_("Unable to play this file in-app. Use Ctrl+Enter to download."))

    def _mediaDownloadSelected(self):
        item = self._getSelectedMediaItem()
        if not item:
            return
        url = str(item.get("url", "") or "")
        label = str(item.get("label", "") or "media")
        if not url:
            return

        defaultName = re.sub(r"[\\/:*?\"<>|]+", "_", label) or "media"
        with wx.FileDialog(
            self,
            message=_("Save media file"),
            defaultFile=defaultName,
            wildcard=_("All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()

        try:
            self._downloadToPath(url, path)
            ui.message(_("Saved: {0}").format(path))
        except Exception as e:
            ui.message(_("Failed to save: {0}").format(e))

    def onMediaKeyDown(self, evt):
        key = evt.GetKeyCode()

        if key == wx.WXK_RETURN:
            if evt.ControlDown():
                self._mediaDownloadSelected()
            else:
                self._mediaPlaySelected()
            return

        if key == wx.WXK_SPACE:
            self._mediaPauseToggle()
            return

        evt.Skip()

    def onMediaCharHook(self, evt):
        # Some wx.ListBox builds don't reliably fire EVT_KEY_DOWN for Enter/Space.
        # EVT_CHAR_HOOK is more consistent across NVDA's bundled wxPython.
        key = evt.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if evt.ControlDown():
                self._mediaDownloadSelected()
            else:
                self._mediaPlaySelected()
            return
        if key == wx.WXK_SPACE:
            self._mediaPauseToggle()
            return
        evt.Skip()

    def _setStatusText(self, text):
        self.article.SetValue(text)
        try:
            self.article.SetInsertionPoint(0)
        except Exception:
            pass

    def _clearResults(self):
        try:
            self.results.DeleteChildren(self._root)
        except Exception:
            pass
        self._treeDataByItem.clear()
        self._treeItemByKey.clear()
        self._currentItem = None
        self._currentUrl = None
        self.openBtn.Enable(False)

        # Media
        try:
            self._mediaStop()
        except Exception:
            pass
        try:
            self._mediaItems = []
            self.mediaList.Set([])
            self.mediaList.Enable(False)
            self._setMediaPanelVisible(False)
        except Exception:
            pass

    def _setMediaPanelVisible(self, visible):
        try:
            self.mediaPanel.Show(bool(visible))
            self.Layout()
        except Exception:
            pass

    def _setTreeItemMediaIndicator(self, item, hasMedia):
        try:
            title = str(item.get("title") or "")
            source = str(item.get("source") or "")
            treeItem = self._treeItemByKey.get((source, title.strip().lower()))
            if treeItem is None or not treeItem.IsOk():
                return
            label = title + (_(" [media]") if hasMedia else "")
            self.results.SetItemText(treeItem, label)
        except Exception:
            pass

    def onClear(self, evt):
        self.query.SetValue("")
        self._clearResults()
        self._setStatusText("")
        wx.CallAfter(self.query.SetFocus)

    def onClose(self, evt):
        # Hide to preserve results unless user clears
        try:
            self.Hide()
        except Exception:
            pass

    def onSearch(self, evt):
        q = self.query.GetValue().strip()
        if not q:
            ui.message(_("Type a word to search."))
            _playSound("error.wav")
            return

        _playSound("search.wav")
        self.searchBtn.Enable(False)
        self._clearResults()
        self._setStatusText(_("Searching…"))

        t = threading.Thread(target=self._searchWorker, args=(q,), daemon=True)
        t.start()

    def _searchWorker(self, q):
        try:
            items = _superSearch(q)
        except Exception as e:
            wx.CallAfter(self._onSearchDone, q, [], str(e))
            return
        wx.CallAfter(self._onSearchDone, q, items, None)

    def _onSearchDone(self, q, items, err):
        self.searchBtn.Enable(True)

        if err:
            _playSound("error.wav")
            self._setStatusText(_("Search failed: {0}").format(err))
            wx.CallAfter(self.query.SetFocus)
            return

        if not items:
            _playSound("error.wav")
            self._setStatusText(_("No results found."))
            wx.CallAfter(self.query.SetFocus)
            return

        # Group into buckets, but preserve source priority order.
        single = _isSingleWord(q)
        sourceOrder = _displaySourceOrder()
        grouped = dict((source, []) for source in sourceOrder)
        for it in items:
            src = str(it.get("source") or "")
            title = str(it.get("title") or "")
            if src in grouped and title:
                grouped[src].append(title)

        firstLeaf = None
        bucketItems = {}

        for src in sourceOrder:
            titles = grouped.get(src) or []
            if not titles:
                continue
            bucket = self.results.AppendItem(self._root, _("{0} ({1})").format(src, len(titles)))
            bucketItems[src] = bucket
            # Always expand buckets after each new search (your requirement).
            self.results.Expand(bucket)

            for title in titles:
                leaf = self.results.AppendItem(bucket, title)
                data = {"title": title, "source": src}
                self._treeDataByItem[leaf] = data
                self._treeItemByKey[(src, title.strip().lower())] = leaf
                if firstLeaf is None:
                    firstLeaf = leaf

        try:
            ui.message(_("{0} results.").format(len(items)))
        except Exception:
            pass

        # Select exact match if present (prefer primary source).
        qnorm = q.strip().lower()
        exact = None
        for source in _prioritySourceOrder(single):
            exact = self._treeItemByKey.get((source, qnorm))
            if exact is not None:
                break

        if exact is not None:
            self.results.SelectItem(exact)
            self.results.SetFocus()
            try:
                self.results.EnsureVisible(exact)
            except Exception:
                pass
            # Auto-load exact match and focus article.
            data = self._treeDataByItem.get(exact)
            if data:
                self._loadItem(data, originalQuery=q, focusArticle=True)
        else:
            if firstLeaf is not None:
                self.results.SelectItem(firstLeaf)
                try:
                    self.results.EnsureVisible(firstLeaf)
                except Exception:
                    pass
                self.results.SetFocus()
            self._setStatusText(_("Use arrow keys to select a result, then press Enter to load."))

    def _getSelectedLeafData(self):
        item = self.results.GetSelection()
        if not item or not item.IsOk():
            return None
        return self._treeDataByItem.get(item)

    def onActivateTreeItem(self, evt):
        item = evt.GetItem() if evt else self.results.GetSelection()
        if not item or not item.IsOk():
            return

        data = self._treeDataByItem.get(item)
        if data:
            self._loadItem(data, originalQuery=self.query.GetValue().strip(), focusArticle=True)
            return

        # Bucket or root: toggle expand/collapse
        if self.results.IsExpanded(item):
            self.results.Collapse(item)
        else:
            self.results.Expand(item)

    def onResultsKeyDown(self, evt):
        key = evt.GetKeyCode()
        ctrl = bool(evt.ControlDown() or wx.GetKeyState(wx.WXK_CONTROL))

        if ctrl and key == ord('C'):
            self.copySelected()
            return

        if key == wx.WXK_RETURN or key == wx.WXK_NUMPAD_ENTER:
            if ctrl:
                self.openSelectedInBrowser()
                return
            self.onActivateTreeItem(None)
            return

        evt.Skip()

    def onResultsCharHook(self, evt):
        key = evt.GetKeyCode()
        ctrl = bool(evt.ControlDown() or wx.GetKeyState(wx.WXK_CONTROL))
        if ctrl and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.openSelectedInBrowser()
            return
        evt.Skip()

    def copySelected(self):
        data = self._getSelectedLeafData()
        if not data:
            return
        title = str(data.get("title") or "")
        source = str(data.get("source") or "")

        if source in ("Wikipedia", "Uncyclopedia"):
            text = f"{title}\n{_sourceEntryUrl(source, title)}"
        else:
            # Dictionary-style sources copy just the word/phrase for spelling or reuse.
            text = title

        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()
            ui.message(_("Copied."))
        else:
            ui.message(_("Clipboard unavailable."))

    def openSelectedInBrowser(self):
        """Open the currently focused result in the browser (tree selection), regardless of what is loaded."""
        data = self._getSelectedLeafData()
        if not data:
            # If a bucket is selected, do nothing.
            ui.message(_("Select an entry to open."))
            return
        title = str(data.get("title") or "")
        source = str(data.get("source") or "")
        url = _sourceEntryUrl(source, title)
        if url:
            webbrowser.open(url)

    def onOpenBrowser(self, evt):
        """Open the currently loaded entry in the browser (button behavior)."""
        url = getattr(self, "_currentUrl", None)
        if url:
            webbrowser.open(url)
            return
        # If nothing loaded yet, fall back to the selected leaf.
        self.openSelectedInBrowser()

    def _loadItem(self, item, originalQuery="", focusArticle=False):
        self._currentItem = item
        self.openBtn.Enable(True)
        self._setStatusText(_("Loading…"))
        t = threading.Thread(target=self._loadWorker, args=(item, originalQuery, focusArticle), daemon=True)
        t.start()

    def _loadWorker(self, item, originalQuery, focusArticle):
        title = str(item.get("title", "") or "")
        source = str(item.get("source", "") or "")

        try:
            if source == "Wikipedia":
                text = _wpSummary(title)
                url = _wpEntryUrl(title)
                media = _wpMediaFiles(title)
                wx.CallAfter(
                    self._onLoadDone, item, originalQuery, "", text, "", [], [], media,
                    _coerce_bool(_s().get("autoOpenInBrowser", False), False), url, focusArticle, None
                )
                return

            if source == "Uncyclopedia":
                text = _uncyclopediaSummary(title)
                url = _uncyclopediaEntryUrl(title)
                media = _uncyclopediaMediaFiles(title)
                wx.CallAfter(
                    self._onLoadDone, item, originalQuery, "", text, "", [], [], media,
                    _coerce_bool(_s().get("autoOpenInBrowser", False), False), url, focusArticle, None
                )
                return

            if source == "Urban Dictionary":
                defs = _urbanDictionaryDefinitions(title)
                url = _urbanDictionaryEntryUrl(title)
                wx.CallAfter(
                    self._onLoadDone, item, originalQuery, "", defs, "", [], [], [],
                    _coerce_bool(_s().get("autoOpenInBrowser", False), False), url, focusArticle, None
                )
                return

            # Wiktionary
            langUsed, defs, langLabel, syns, ants = _lookupDefinitions(title)
            url = _entryUrl(title)
            wx.CallAfter(
                self._onLoadDone,
                item, originalQuery, langUsed, defs, langLabel, syns, ants,
                _wtMediaFiles(title),
                _coerce_bool(_s().get("autoOpenInBrowser", False), False),
                url, focusArticle, None
            )
        except Exception as e:
            wx.CallAfter(self._onLoadDone, item, originalQuery, None, [], "Unknown", [], [], [], False, None, focusArticle, str(e))

    def _onLoadDone(self, item, originalQuery, langUsed, defsOrText, langLabel, syns, ants, mediaItems, autoOpen, url, focusArticle, err):
        title = str(item.get("title", "") or "")
        source = str(item.get("source", "") or "")

        if err:
            self._setStatusText(_("Failed to load: {0}").format(err))
            _playSound("error.wav")
            return

        self._currentUrl = url or _sourceEntryUrl(source, title)

        q = (originalQuery or "").strip()
        header = []
        if q:
            header.append(_("Query: {0}").format(q))
        header.append(_("Source: {0}").format(source))
        if source == "Wiktionary" and (langLabel or ""):
            header.append(_("Language: {0}").format(langLabel))
        header.append("")

        body = ""

        if source in ("Wikipedia", "Uncyclopedia"):
            text = str(defsOrText or "").strip()
            body = text if text else _("No summary text was returned.")
        elif source == "Urban Dictionary":
            defs = defsOrText if isinstance(defsOrText, list) else []
            maxDefs = _getInt(_s().get("maxDefinitions", 50), 50, 1, 50)
            defs = defs[:maxDefs]
            if defs:
                parts = []
                for i, d in enumerate(defs, start=1):
                    section = [f"{i}. {str(d.get('definition') or '').strip()}"]
                    example = str(d.get("example") or "").strip()
                    if example:
                        section.append(_("Example: {0}").format(example))
                    votes = _("Votes: +{0} / -{1}").format(int(d.get("thumbsUp") or 0), int(d.get("thumbsDown") or 0))
                    section.append(votes)
                    author = str(d.get("author") or "").strip()
                    if author:
                        section.append(_("Author: {0}").format(author))
                    parts.append("\n".join(section).strip())
                body = "\n\n".join(parts).strip()
            else:
                body = _("No Urban Dictionary definitions were returned.")
        else:
            defs = defsOrText if isinstance(defsOrText, list) else []
            maxDefs = _getInt(_s().get("maxDefinitions", 50), 50, 1, 50)
            defs = defs[:maxDefs]
            if defs:
                for i, d in enumerate(defs, start=1):
                    body += f"{i}. {d}\n"
            else:
                body = _("No definitions found in the preferred section.")

            syns = syns or []
            ants = ants or []
            if syns:
                body += "\n\n" + _("Synonyms: ") + ", ".join(syns) + "\n"
            if ants:
                body += _("Antonyms: ") + ", ".join(ants) + "\n"

        textOut = "\n".join(header) + body.strip() + "\n"
        self.article.SetValue(textOut)
        try:
            self.article.SetInsertionPoint(0)
        except Exception:
            pass


        # Media list
        try:
            self._mediaStop()
        except Exception:
            pass
        self._mediaItems = mediaItems if isinstance(mediaItems, list) else []
        self._setTreeItemMediaIndicator(item, bool(self._mediaItems))
        try:
            labels = [str(it.get("label", "") or str(it.get("title", "") or "")) for it in self._mediaItems]
            self.mediaList.Set(labels)
            self.mediaList.Enable(bool(labels))
            self._setMediaPanelVisible(bool(labels))
        except Exception:
            pass

        ui.message(_("Loaded: {0}").format(title))
        _playSound("done.wav")

        if autoOpen and self._currentUrl:
            try:
                webbrowser.open(self._currentUrl)
            except Exception:
                pass

        if focusArticle:
            wx.CallAfter(self.article.SetFocus)



# ---- Global plugin ----
class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self._dlg = None

    @scriptHandler.script(description=_("Open Wiki Fusion"))
    def script_openWikiFusion(self, gesture):
        if self._dlg and self._dlg.IsShown():
            try:
                self._dlg.Raise()
                self._dlg.SetFocus()
                return
            except Exception:
                pass
        if self._dlg is None:
            self._dlg = WikiFusionDialog(gui.mainFrame)
        self._dlg.Show()
        self._dlg.Raise()
        wx.CallAfter(self._dlg.query.SetFocus)

    __gestures = {
        "kb:NVDA+alt+i": "openWikiFusion",
    }
