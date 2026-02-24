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
import nvwave
import os

addonHandler.initTranslation()

ADDON_NAME = "wikiFusion"

# ---- Config ----
_CONFIG_SPEC = {
    "soundsEnabled": "boolean(default=True)",
    "siteLangCode": "string(default=en)",
    "wpLangCode": "string(default=en)",
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

def _httpGetJson(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "wikiFusion (NVDA addon)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8", errors="replace"))

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

    # Fetch both. Keep them independent so the UI can show buckets.
    wt = []
    wp = []
    try:
        wt = _opensearch(q)  # Wiktionary titles
    except Exception:
        wt = []
    try:
        wp = _wpOpensearch(q)  # Wikipedia titles
    except Exception:
        wp = []

    if single:
        primary = [{"title": t, "source": "Wiktionary"} for t in wt]
        secondary = [{"title": t, "source": "Wikipedia"} for t in wp]
    else:
        primary = [{"title": t, "source": "Wikipedia"} for t in wp]
        secondary = [{"title": t, "source": "Wiktionary"} for t in wt]

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

    return _dedup(primary) + _dedup(secondary)




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

        # Results (Tree: Wikipedia / Wiktionary buckets)
        self.results = wx.TreeCtrl(
            self,
            style=wx.TR_HIDE_ROOT | wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_SINGLE | wx.BORDER_SUNKEN
        )
        mainSizer.Add(self.results, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

        # Article
        self.article = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SUNKEN)
        mainSizer.Add(self.article, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 5)

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

        self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)
        self.Bind(wx.EVT_CLOSE, self.onClose)

        self.openBtn.Enable(False)

        wx.CallAfter(self.query.SetFocus)

    def onCharHook(self, evt):
        key = evt.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.onClose(None)
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

        # Group into buckets, but preserve priority order (primary bucket first).
        single = _isSingleWord(q)
        primary = "Wiktionary" if single else "Wikipedia"
        secondary = "Wikipedia" if single else "Wiktionary"

        grouped = {"Wikipedia": [], "Wiktionary": []}
        for it in items:
            src = str(it.get("source") or "")
            title = str(it.get("title") or "")
            if src in grouped and title:
                grouped[src].append(title)

        order = [primary, secondary]

        firstLeaf = None
        bucketItems = {}

        for src in order:
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
        exact = self._treeItemByKey.get((primary, qnorm)) or self._treeItemByKey.get((secondary, qnorm))

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

        if source == "Wikipedia":
            text = f"{title}\n{_wpEntryUrl(title)}"
        else:
            # Wiktionary: copy just the word/phrase for spelling use.
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
        url = _wpEntryUrl(title) if source == "Wikipedia" else _entryUrl(title)
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
                wx.CallAfter(self._onLoadDone, item, originalQuery, "", text, "", [], [], False, url, focusArticle, None)
                return

            # Wiktionary
            langUsed, defs, langLabel, syns, ants = _lookupDefinitions(title)
            url = _entryUrl(title)
            wx.CallAfter(
                self._onLoadDone,
                item, originalQuery, langUsed, defs, langLabel, syns, ants,
                _coerce_bool(_s().get("autoOpenInBrowser", False), False),
                url, focusArticle, None
            )
        except Exception as e:
            wx.CallAfter(self._onLoadDone, item, originalQuery, None, [], "Unknown", [], [], False, None, focusArticle, str(e))

    def _onLoadDone(self, item, originalQuery, langUsed, defsOrText, langLabel, syns, ants, autoOpen, url, focusArticle, err):
        title = str(item.get("title", "") or "")
        source = str(item.get("source", "") or "")

        if err:
            self._setStatusText(_("Failed to load: {0}").format(err))
            _playSound("error.wav")
            return

        self._currentUrl = url or (_entryUrl(title) if source == "Wiktionary" else _wpEntryUrl(title))

        q = (originalQuery or "").strip()
        header = []
        if q:
            header.append(_("Query: {0}").format(q))
        header.append(_("Source: {0}").format(source))
        if source == "Wiktionary" and (langLabel or ""):
            header.append(_("Language: {0}").format(langLabel))
        header.append("")

        body = ""

        if source == "Wikipedia":
            text = str(defsOrText or "").strip()
            body = text if text else _("No summary text was returned.")
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
