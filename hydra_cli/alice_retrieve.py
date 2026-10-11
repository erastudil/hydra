"""Alice retrieval over the stacks and the web whitelist.

Passages are sentences inside paragraphs. A passage score is a sum of relation
weights: rare terms, adjacent phrases, entity names, aliases, headings,
paragraph context, definitions, answer-type cues, and source tier. Weights add.
No weight scales another.

A passage answers when its sum reaches THRESHOLD of the query mass, the summed
weight of the question's own relations. Below that, Alice fetches whitelisted
pages, indexes them, and searches again. Still below, she asks one question and
adds the reply's terms to the same sum.

Run: python -m hydra_cli.alice_retrieve build | stats | ask QUESTION | chat
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from hydra_cli.alice_senses import public_https
from hydra_cli.config import hydra_home

INDEX_VERSION = "6"
USER_AGENT = "alice-hydra/1.2 (+https://github.com/erastudil/hydra)"

THRESHOLD = float(os.environ.get("ALICE_THRESHOLD", "0.6") or 0.6)
FLOOR = 3.0
ANCHOR_SHARE = 0.5
COVER_SHARE = 0.7
MAX_ROUNDS = 2
MAX_ANSWERS = 3
PENDING_TTL = 900.0
LOOKUP_TTL = 7 * 86400.0
PAGE_TTL = 30 * 86400.0

W_PHRASE = 2.0
W_QUOTED = 3.0
W_ENTITY = 4.0
W_ALIAS = 2.5
W_RELATED = 0.75
W_LEARNED = 1.0
W_DERIVED = 2.0
W_LEAD = 1.0
LEAD_PARAS = 3
W_HEADING = 1.5
W_CONTEXT = -3.0
RARITY_MIN = 0.5
RARITY_MAX = 9.0
CUE_WEIGHT = {"who": 2.0, "when": 2.5, "where": 1.5, "count": 2.0, "why": 1.5, "what": 0.5}
CUE_IN_MASS = {"who", "when", "where", "count", "why"}
KIND_GROUP = {"entity": "name", "alias": "name", "about": "name", "related": "related", "derived": "derived"}
GROUP_CAP = {"name": 5.0, "related": 1.5, "derived": 4.0}
FINAL_CANDIDATES = 1200
TIER_BONUS = {"primary": 1.0, "curated": 0.6, "seed": 0.3, "synthesized": 0.0, "speculative": -1.0}

# Same classes as snowgate-alice/mind/whitelist.json. That file merges in when present.
WHITELIST: Dict[str, List[str]] = {
    "metrology": ["nist.gov", "bipm.org", "codata.org", "iupac.org", "ciaaw.org", "bnl.gov"],
    "open-textbooks": ["openstax.org", "ocw.mit.edu", "libretexts.org", "phet.colorado.edu", "gutenberg.org"],
    "standards": ["rfc-editor.org", "ietf.org", "w3.org", "whatwg.org", "ecma-international.org", "tc39.es",
                  "unicode.org", "ieee.org", "iso.org"],
    "platform-docs": ["docs.python.org", "developer.mozilla.org", "doc.rust-lang.org", "kotlinlang.org", "nodejs.org"],
    "government-primary": ["congress.gov", "supremecourt.gov", "uscode.house.gov", "govinfo.gov", "census.gov",
                           "data.census.gov", "data.gov", "weather.gov", "cdc.gov", "nih.gov", "ncbi.nlm.nih.gov",
                           "nasa.gov", "usgs.gov", "sec.gov", "who.int", "worldbank.org", "imf.org"],
    "journals-preprints": ["arxiv.org", "doi.org", "pubmed.ncbi.nlm.nih.gov", "pubchem.ncbi.nlm.nih.gov"],
    "academic-encyclopedias": ["plato.stanford.edu", "iep.utm.edu", "philpapers.org", "philarchive.org",
                               "inphoproject.org", "ndpr.nd.edu", "britannica.com"],
    "knowledge-graphs": ["wikidata.org", "dbpedia.org", "conceptnet.io", "openstreetmap.org"],
    "encyclopedic-seed": ["wikipedia.org", "wikisource.org", "wikiquote.org", "wikimedia.org"],
}
DENY = ["chegg.com", "coursehero.com", "quizlet.com", "brainly.com", "studocu.com", "scribd.com",
        "medium.com", "quora.com", "reddit.com"]
CLASS_TIER = {
    "metrology": "primary", "open-textbooks": "primary", "standards": "primary", "platform-docs": "primary",
    "government-primary": "primary", "academic-encyclopedias": "primary", "journals-preprints": "curated",
    "knowledge-graphs": "curated", "encyclopedic-seed": "seed",
}

STOP = set("""
a about above after again against all also am an and any are aren as at be because been before being below
between both but by can cannot could couldn did didn do does doesn doing don down during each few for from
further had hadn has hasn have haven having he her here hers herself him himself his how i if in into is isn
it its itself just let me more most my myself no nor not now of off on once only or other ought our ours
ourselves out over own same shall she should so some such than that the their theirs them themselves then
there these they this those through to too under until up upon us very was wasn we were weren what whats
when where which while who whom whose why will with won would you your yours yourself yourselves
tell explain describe define please give show list name kind sort thing things something someone anyone
know need want mean means meaning called exam quiz homework question answer briefly simple simply really
many much
""".split())

WORK_WORDS = set("""
fix bug bugs error errors failure failures fail failing test tests build crash crashes issue issues problem
problems file files code repo branch commit diff patch function method class script output log logs trace
traceback stack it this that thing change changes run running broken works working
""".split())

SYNONYMS = {
    "wwi": "world war i", "ww1": "world war i", "wwii": "world war ii", "ww2": "world war ii",
    "usa": "united states", "uk": "united kingdom", "ussr": "soviet union", "un": "united nations",
    "eu": "european union", "dna": "deoxyribonucleic acid", "rna": "ribonucleic acid",
    "bc": "bce", "bce": "bc", "ad": "ce", "ce": "ad", "jfk": "john kennedy", "fdr": "franklin roosevelt",
    "mlk": "martin luther king",
}

LEMMA = {
    "died": "die", "dies": "die", "dying": "die", "wrote": "write", "written": "write", "writes": "write",
    "writing": "write", "began": "begin", "begun": "begin", "fought": "fight", "won": "win", "led": "lead",
    "ran": "run", "gave": "give", "given": "give", "took": "take", "taken": "take", "made": "make",
    "became": "become", "thought": "think", "went": "go", "gone": "go", "sent": "send", "built": "build",
    "held": "hold", "known": "know", "knew": "know", "saw": "see", "seen": "see", "spoke": "speak",
    "spoken": "speak", "brought": "bring", "taught": "teach", "drew": "draw", "drawn": "draw", "grew": "grow",
    "grown": "grow", "rose": "rise", "risen": "rise", "fell": "fall", "fallen": "fall", "struck": "strike",
    "chose": "choose", "chosen": "choose", "stood": "stand", "understood": "understand", "ruled": "rule",
    "children": "child", "men": "man", "women": "woman", "wives": "wife", "mice": "mouse",
    "feet": "foot", "teeth": "tooth", "geese": "goose", "data": "datum", "criteria": "criterion",
    "phenomena": "phenomenon",
}

KEEP_WHOLE = {"hundred", "sacred", "kindred", "naked", "wicked", "rugged", "beloved", "thing", "during", "news",
               "physics", "mathematics", "economics", "politics", "ethics", "genetics", "species", "series", "always"}

SEMANTIC = {
    "die": ["death", "dead"], "death": ["die", "dead"], "dead": ["die", "death"], "born": ["birth"],
    "birth": ["born"], "kill": ["assassinate", "murder"], "assassinate": ["kill"], "begin": ["start"],
    "start": ["begin"], "end": ["finish"], "invent": ["inventor", "invention"], "write": ["author"],
    "author": ["write"], "rule": ["reign"], "reign": ["rule"], "live": ["reside", "residence", "home"],
    "reside": ["live", "residence"], "residence": ["live", "reside"],
}
DERIVE_SUFFIXES = ("y", "ion", "ation", "or", "er", "ery", "ment", "al", "ic", "ist", "ism", "ive", "ity", "ance", "ence",
                   "an", "ian")
LIFESPAN = re.compile(r"\([^()]*\b\d{3,4}\b[^()]*[–—-][^()]*\b\d{2,4}\b[^()]*\)")
LIFE_TERMS = {"die", "death", "born", "birth"}
PRONOUN_START = re.compile(r"^(He|She|His|Her|They|Their|It|Its)\b")

TECH_WORDS = set("""
html css javascript js dom http https api browser web json fetch async promise flexbox grid element
attribute selector python node npm typescript
""".split())

SKIP_SECTIONS = {"see also", "references", "notes", "external links", "further reading", "bibliography",
                 "sources", "citations", "works cited", "footnotes", "notes and references"}

EXCLUDED_SHELVES = {"math_proofs", "reasoning_chains", "dialect_tuning", "rfc_specs", "verified_code"}
SKIP_ROOT_FILES = {"INDEX.md", "FACTS_INDEX.md", "TRUSTED_SOURCES.md", "LAW.md"}

CARDS_PATH = Path(__file__).resolve().parent / "data" / "stacks_cards.jsonl"

INQUIRY_RE = re.compile(
    r"^(who|whom|whose|what|whats|when|where|which|why|how|is|are|was|were|did|does|do|can|could|name|list|"
    r"define|explain|describe|tell me|give me|summarize|identify)\b",
    re.I,
)
CODE_RE = re.compile(r"`|\b[\w./\\-]+\.(py|js|ts|tsx|json|md|toml|yaml|yml|rs|go|java|c|cpp|h|sh|ps1)\b|traceback|line \d+", re.I)
DEFINITIONAL_RE = re.compile(
    r"^(who|what)\s+(is|was|are|were)\b|^(define|describe|tell me about|explain what|what does .+ mean)\b", re.I
)
RELATIONAL_RE = re.compile(
    r"^(?:what|who|which)\s+(?:is|was|are|were)\s+(?:the|a|an)\s+(?P<head>[\w-]+(?:\s+[\w-]+)?)\s+of\s+(?P<obj>.+)", re.I
)
AGENT_RE = re.compile(r"^who\s+(?P<verb>[a-z]+)\s+(?P<obj>.+)", re.I)
DEFINIENDUM_RE = re.compile(r"^(?:what|who)\s+(?:is|was|are|were)\s+(?:the\s+|a\s+|an\s+)?(.+?)\??$", re.I)
WORK_NOUNS = r"(?:book|novel|poem|play|treatise|work|essay|painting|portrait|symphony|opera|song|album|film|dialogue|sculpture)"
COUNT_RE = re.compile(r"^how\s+many\s+(?P<noun>[a-z]+)\b", re.I)
STRONG_WHERE = re.compile(r"\b(located|situated|lies|lying|based|headquartered|stands|sits)\s+(at|in|on|near|along)\b"
                          r"|\b(?:born|died|buried)\s+(?:at|in)\s+(?:the\s+)?(?-i:[A-Z])", re.I)
COPULA = r"(?:is|was|are|were|became|remains|has been|had been|have been|serves as|served as)"
AUXILIARY = {"is", "was", "were", "are", "did", "does", "do", "has", "had", "have", "will", "can", "could", "would", "should"}
MANDATORY = {"first", "last", "largest", "smallest", "oldest", "youngest", "tallest", "highest", "lowest", "longest",
             "shortest", "biggest", "second", "third", "fourth", "fifth", "earliest", "latest", "deepest", "fastest"}
W_SLOT = 3.0
REQUIRED_CUES = {"when", "where", "count"}
W_STRONG_WHERE = 3.0
W_VOTE = 1.0
W_SUBJECT = 1.5
MIN_CONCEPT_FAME = 2
VOTE_CAP = 3.0
RELATION_NOUNS = {
    "capital", "president", "author", "founder", "inventor", "leader", "king", "queen", "ruler", "emperor",
    "population", "currency", "language", "mother", "father", "wife", "husband", "son", "daughter", "brother",
    "sister", "chancellor", "minister", "governor", "mayor", "director", "owner", "creator", "composer", "painter",
    "architect", "city", "river", "mountain", "symbol", "flag", "motto", "anthem", "successor", "predecessor",
}
PERSON_FRAME = re.compile(
    r"^(?:who\s+(?:is|was|were|are)\s+(?P<a>.+)"
    r"|(?:when|where|how)\s+(?:was|did|were|is)\s+(?P<b>.+?)\s+(?:born|die|died|live|lived|rule|ruled|reign|reigned|buried)\b"
    r"|what\s+(?:did|does|is)\s+(?P<c>.+?)\s+(?:do|write|discover|invent|say|believe|argue|paint|compose|known for|famous for)\b"
    r"|(?:tell me about|describe)\s+(?P<d>.+))",
    re.I,
)
DEFINE_MARK = re.compile(r"\b(was|is|are|were|refers to|denotes|means)\b|\(born|\(c\.|\(\s*\d| : | – | — ")
PERSON_DESC = re.compile(
    r"\(\s*(?:born\s+)?c?\.?\s*\d{1,4}|\b\d{3,4}\s*[–-]\s*\d{2,4}\b|\b(politician|philosopher|president|king|queen|"
    r"emperor|empress|general|scientist|physicist|chemist|biologist|mathematician|writer|poet|novelist|"
    r"playwright|painter|sculptor|composer|explorer|inventor|leader|saint|pope|prophet|theologian|economist|"
    r"historian|statesman|stateswoman|activist|actor|actress|singer|pharaoh|monarch|sultan|caliph|tsar|czar)\b",
    re.I,
)
CUE_PATTERNS = {
    "who": re.compile(r"(?i:\b(born|died|king|queen|emperor|empress|president|philosopher|scientist|physicist|"
                      r"chemist|biologist|mathematician|writer|poet|author|painter|composer|explorer|inventor)\b)|"
                      r"\(\s*(?:c\.\s*)?\d{3,4}\s*[–—-]|\b\d{3,4}\s*[–—-]\s*\d{2,4}\b|\bby [A-Z][a-z]{2,}|"
                      r"(?<=[a-z,;] )[A-Z][a-z]+(?:\s+[A-Z]\.)?\s+(?:(?:de|da|von|van|of)\s+)?[A-Z][a-z]{2,}"),
    "when": re.compile(r"\b\d{3,4}\b|\b\d{1,4}\s*(?:bce|bc|ce|ad)\b|\bcentur(?:y|ies)\b", re.I),
    "where": re.compile(r"\b(located|situated|capital|city|country|region|province|island|river|continent|"
                        r"kingdom|empire|near|north|south|east|west)\b", re.I),
    "count": re.compile(r"\b\d[\d,.]*\b|\b(two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|"
                        r"million|billion)\b", re.I),
    "why": re.compile(r"\b(because|due to|caused|causes|cause|reason|led to|result of|resulted|in order to|so that)\b", re.I),
    "what": re.compile(r"\b(is|are|was|were|refers to|means|defined as|denotes|describes)\b", re.I),
}
TOKEN_RE = re.compile(r"[a-z0-9]+")
_ABBR = re.compile(r"\b(e\.g|i\.e|c|ca|cf|vs|etc|dr|mr|mrs|ms|st|jr|sr|no|vol|pp|fig|approx|u\.s|u\.k|b\.c|a\.d)\.", re.I)
_SPLIT = re.compile(r"(?<=[.!?])(?<![A-Z]\.)\s+(?=[\"“(\[]?[A-Z0-9])")
_SUFFIXES = (("ies", "y"), ("sses", "ss"), ("ches", "ch"), ("shes", "sh"), ("xes", "x"), ("ing", ""),
             ("ed", ""), ("ss", "ss"), ("us", "us"), ("is", "is"), ("s", ""))


# ---------------------------------------------------------------- text


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def words(text: str) -> List[str]:
    return [tok for tok in TOKEN_RE.findall(fold(text)) if len(tok) > 1 or tok.isdigit()]


def stem(word: str) -> str:
    word = LEMMA.get(word, word)
    if len(word) <= 3 or word.isdigit() or word in KEEP_WHOLE:
        return word
    for suffix, repl in _SUFFIXES:
        if word.endswith(suffix):
            if suffix == "ed" and (word.endswith("eed") or not re.search(r"[aeiouy]", word[:-2])):
                return word
            base = word[: -len(suffix)] + repl
            if len(base) < 3:
                return word
            if suffix in ("ing", "ed") and len(base) >= 4 and base[-1] == base[-2] and base[-1] not in "lsz":
                base = base[:-1]
            return base
    return word


def content(text: str) -> List[str]:
    return [stem(tok) for tok in words(text) if tok not in STOP]


def bigrams(terms: Sequence[str]) -> List[str]:
    return [f"{a} {b}" for a, b in zip(terms, terms[1:]) if a != b]


def sentences(paragraph: str) -> List[str]:
    protected = _ABBR.sub(lambda m: m.group(0).replace(".", "\u2024"), paragraph)
    parts = _SPLIT.split(protected)
    return [part.replace("\u2024", ".").strip() for part in parts if len(part.strip()) >= 3]


def clean_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?<!\w)\*(?!\s)([^*]+)(?<!\s)\*(?!\w)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_html(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?is)<sup[^>]*>.*?</sup>", " ", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(fragment)).strip()


def is_inquiry(prompt: str) -> bool:
    text = prompt.strip()
    if not text or CODE_RE.search(text):
        return False
    if not (INQUIRY_RE.match(text) or text.endswith("?")):
        return False
    terms = [tok for tok in words(text) if tok not in STOP]
    if not terms:
        return False
    return not all(tok in WORK_WORDS for tok in terms)


def answer_cue(question: str) -> Optional[str]:
    q = fold(question).strip()
    if re.match(r"^(who|whom|whose)\b", q):
        return "who"
    if re.match(r"^(when|what year|in what year|which year|what century|in which century|what date)\b", q):
        return "when"
    if re.match(r"^(where|in what country|in which country|what country|which country|what city|which city)\b", q):
        return "where"
    if re.match(r"^(how many|how much|how long|how old|how far|how big|what percentage)\b", q):
        return "count"
    if re.match(r"^why\b", q):
        return "why"
    if re.match(r"^(what|define|describe|explain|tell me about)\b", q):
        return "what"
    return None


def focus_words(text: str) -> List[str]:
    return [tok for tok in words(text) if tok not in STOP]


# ---------------------------------------------------------------- whitelist


_WHITELIST_LOCK = threading.Lock()
_WHITELIST_READY = False


def _merge_whitelist_file() -> None:
    global _WHITELIST_READY
    with _WHITELIST_LOCK:
        if _WHITELIST_READY:
            return
        _WHITELIST_READY = True
        candidates = []
        home = os.environ.get("ALICE_HOME", "").strip()
        if home:
            candidates.append(Path(home) / "mind" / "whitelist.json")
        candidates.append(Path(__file__).resolve().parents[2] / "snowgate-alice" / "mind" / "whitelist.json")
        for path in candidates:
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for cls, domains in (data.get("classes") or {}).items():
                merged = WHITELIST.setdefault(cls, [])
                for domain in domains:
                    if domain not in merged:
                        merged.append(domain)
                CLASS_TIER.setdefault(cls, "curated")
            for domain in data.get("deny") or []:
                if domain not in DENY:
                    DENY.append(domain)
            return


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def host_class(url: str) -> Optional[str]:
    _merge_whitelist_file()
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host or any(_host_matches(host, domain) for domain in DENY):
        return None
    for cls, domains in WHITELIST.items():
        if any(_host_matches(host, domain) for domain in domains):
            return cls
    return None


def web_allowed(url: str) -> bool:
    return bool(public_https(url)) and host_class(url) is not None


def tier_for(url: str) -> str:
    return CLASS_TIER.get(host_class(url) or "", "curated")


def _http(url: str, timeout: float = 12.0, limit: int = 3_000_000) -> Optional[Tuple[str, str]]:
    if not web_allowed(url):
        return None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final = response.geturl()
                if not web_allowed(final):
                    return None
                raw = response.read(limit)
                charset = response.headers.get_content_charset() or "utf-8"
                return final, raw.decode(charset, "replace")
        except urllib.error.HTTPError as exc:
            if attempt == 0 and exc.code in (429, 500, 502, 503, 504):
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                time.sleep(min(4.0, float(retry_after)) if retry_after.isdigit() else 1.5)
                continue
            return None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
            if attempt == 0:
                time.sleep(1.0)
                continue
            return None
    return None


def _json(url: str, timeout: float = 12.0) -> Optional[dict]:
    got = _http(url, timeout=timeout)
    if not got:
        return None
    try:
        return json.loads(got[1])
    except ValueError:
        return None


def web_reachable() -> bool:
    data = _json("https://en.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json", timeout=8)
    return bool(data and data.get("query"))


# ---------------------------------------------------------------- pages


@dataclass
class Page:
    url: str
    title: str
    tier: str
    paragraphs: List[Tuple[str, str]]
    single: bool = False


@dataclass
class Entity:
    key: str
    label: str
    descr: str = ""
    wiki: str = ""
    aliases: List[str] = field(default_factory=list)
    origin: str = ""
    fame: int = 0

    @property
    def person(self) -> bool:
        return bool(PERSON_DESC.search(self.descr or ""))


WP_API = "https://en.wikipedia.org/w/api.php"
WD_API = "https://www.wikidata.org/w/api.php"


def wiki_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


def wp_search(query: str, limit: int = 3) -> List[str]:
    params = {"action": "query", "list": "search", "srsearch": query, "srlimit": str(limit), "format": "json"}
    data = _json(WP_API + "?" + urllib.parse.urlencode(params))
    if not data:
        return []
    return [hit["title"] for hit in data.get("query", {}).get("search", []) if hit.get("title")]


def wp_page(title: str) -> Optional[Page]:
    params = {"action": "query", "prop": "extracts", "explaintext": "1", "redirects": "1", "titles": title,
              "format": "json", "formatversion": "2"}
    data = _json(WP_API + "?" + urllib.parse.urlencode(params), timeout=15)
    pages = (data or {}).get("query", {}).get("pages") or []
    if not pages or not pages[0].get("extract"):
        return None
    page = pages[0]
    heading = page["title"]
    paragraphs: List[Tuple[str, str]] = []
    for line in page["extract"][:120_000].splitlines():
        line = line.strip()
        if not line:
            continue
        matched = re.match(r"^=+\s*(.+?)\s*=+$", line)
        if matched:
            section = matched.group(1).strip()
            if section.lower() in SKIP_SECTIONS:
                break
            heading = page["title"] + " · " + section
            continue
        if len(line) >= 40:
            paragraphs.append((heading, line))
        if len(paragraphs) >= 250:
            break
    return Page(wiki_url(page["title"]), page["title"], "seed", paragraphs)


def wd_human_search(query: str, limit: int = 6) -> List[str]:
    """Wikidata items that are instances of human (Q5), ranked by the search engine."""
    params = {"action": "query", "list": "search", "srsearch": f"{query} haswbstatement:P31=Q5",
              "srlimit": str(limit), "format": "json"}
    data = _json(WD_API + "?" + urllib.parse.urlencode(params))
    hits = (data or {}).get("query", {}).get("search") or []
    return [hit["title"] for hit in hits if re.fullmatch(r"Q\d+", hit.get("title", ""))]


MATCH_BONUS = {("label", True): 3.0, ("alias", True): 2.0, ("label", False): 1.0, ("alias", False): 0.0}


def wd_label_search(query: str, limit: int = 8) -> List[str]:
    """Wikidata items whose English label or alias begins with the query's words."""
    return [qid for qid, _ in wd_label_matches(query, limit)]


def wd_label_matches(query: str, limit: int = 8) -> List[Tuple[str, float]]:
    """(item, match bonus): exact label 3, exact alias 2, label prefix 1, alias prefix 0."""
    params = {"action": "wbsearchentities", "search": query, "language": "en", "format": "json",
              "limit": str(limit), "type": "item"}
    data = _json(WD_API + "?" + urllib.parse.urlencode(params))
    wanted = words(query)
    out: List[Tuple[str, float]] = []
    for item in (data or {}).get("search") or []:
        match = item.get("match") or {}
        got = words(match.get("text") or "")
        if got[: len(wanted)] == wanted:
            kind = "label" if match.get("type") == "label" else "alias"
            out.append((item["id"], MATCH_BONUS[(kind, got == wanted)]))
    return out


def wd_entities(ids: Sequence[str]) -> List[Entity]:
    if not ids:
        return []
    params = {"action": "wbgetentities", "ids": "|".join(ids), "props": "labels|descriptions|aliases|sitelinks",
              "languages": "en", "format": "json"}
    data = _json(WD_API + "?" + urllib.parse.urlencode(params), timeout=15)
    bodies = (data or {}).get("entities") or {}
    out: List[Entity] = []
    for qid in ids:
        body = bodies.get(qid) or {}
        label = ((body.get("labels") or {}).get("en") or {}).get("value", "")
        if not label:
            continue
        sitelinks = body.get("sitelinks") or {}
        out.append(Entity(
            key=qid,
            label=label,
            descr=((body.get("descriptions") or {}).get("en") or {}).get("value", ""),
            wiki=(sitelinks.get("enwiki") or {}).get("title", ""),
            aliases=[item.get("value", "") for item in (body.get("aliases") or {}).get("en", []) if item.get("value")][:10],
            origin="wikidata",
            fame=len(sitelinks),
        ))
    return out


def sep_search(query: str, limit: int = 1) -> List[str]:
    got = _http("https://plato.stanford.edu/search/searcher.py?" + urllib.parse.urlencode({"query": query}))
    if not got:
        return []
    seen: List[str] = []
    for path in re.findall(r"entry=(/entries/[a-z0-9\-]+/)", got[1]):
        url = "https://plato.stanford.edu" + path
        if url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


def mdn_search(query: str, limit: int = 1) -> List[str]:
    data = _json("https://developer.mozilla.org/api/v1/search?" + urllib.parse.urlencode({"q": query, "locale": "en-US"}))
    docs = (data or {}).get("documents") or []
    return ["https://developer.mozilla.org" + doc["mdn_url"] for doc in docs[:limit] if doc.get("mdn_url")]


def html_page(url: str) -> Optional[Page]:
    got = _http(url, timeout=15)
    if not got:
        return None
    final, body = got
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    title = strip_html(title_match.group(1)) if title_match else final
    main = re.search(r'(?is)<(?:main|article)[^>]*>(.*)</(?:main|article)>', body)
    scope = main.group(1) if main else body
    scope = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", scope)
    heading = title
    paragraphs: List[Tuple[str, str]] = []
    for tag, inner in re.findall(r"(?is)<(h[1-4]|p|li|dd|blockquote)\b[^>]*>(.*?)</\1>", scope):
        text = strip_html(inner)
        if tag.lower().startswith("h"):
            if text.lower() in SKIP_SECTIONS:
                break
            if text:
                heading = title + " · " + text
            continue
        if len(text) >= (60 if tag.lower() == "p" else 40):
            paragraphs.append((heading, text))
        if len(paragraphs) >= 300:
            break
    if not paragraphs:
        return None
    return Page(final, title, tier_for(final), paragraphs)


# ---------------------------------------------------------------- stacks sources


def stacks_root() -> Optional[Path]:
    env = os.environ.get("ALICE_STACKS", "").strip()
    candidates = [Path(env)] if env else []
    candidates.append(Path(__file__).resolve().parents[2] / "hnai" / "easylm" / "stacks")
    candidates.append(Path.home() / "Documents" / "hnai" / "easylm" / "stacks")
    for path in candidates:
        if path.is_dir() and any(path.glob("*/FACTS.md")):
            return path
    return None


def _stack_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for path in sorted(root.glob("*.md")):
        if path.name not in SKIP_ROOT_FILES:
            files.append(path)
    for shelf in sorted(p for p in root.iterdir() if p.is_dir()):
        if shelf.name in EXCLUDED_SHELVES or shelf.name.startswith("."):
            continue
        files.extend(sorted(shelf.rglob("*.md")))
    return files


def markdown_units(text: str) -> Iterator[Tuple[str, str, int, bool]]:
    """Yield (heading, paragraph, line number, single sentence) from a markdown file."""
    lines = text.splitlines()
    start_at = 0
    if lines and lines[0].strip() == "---":
        for number in range(1, len(lines)):
            if lines[number].strip() == "---":
                start_at = number + 1
                break
    heading = ""
    buf: List[str] = []
    first = 0
    fence = False
    table_header: Optional[List[str]] = None
    out: List[Tuple[str, str, int, bool]] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            paragraph = clean_markdown(" ".join(buf))
            if len(paragraph) >= 25:
                out.append((heading, paragraph, first, False))
        buf = []

    for number in range(start_at, len(lines)):
        stripped = lines[number].strip()
        if stripped.startswith("```"):
            flush()
            fence = not fence
            continue
        if fence:
            continue
        if stripped.startswith("#"):
            flush()
            heading = clean_markdown(stripped.lstrip("#"))
            table_header = None
            continue
        if stripped.startswith("|"):
            flush()
            cells = [clean_markdown(cell) for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", cell) or not cell for cell in cells):
                continue
            if table_header is None:
                table_header = cells
                continue
            row = " — ".join(cell for cell in cells if cell)
            if len(row) >= 25:
                out.append((heading, row, number + 1, True))
            continue
        table_header = None
        if not stripped or stripped in ("---", "***"):
            flush()
            continue
        item = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", stripped)
        if item:
            flush()
            buf = [item.group(2)]
            first = number + 1
            continue
        if stripped.startswith(">"):
            stripped = stripped.lstrip("> ")
        if not buf:
            first = number + 1
        buf.append(stripped)
    flush()
    yield from out


def facts_units(text: str) -> Iterator[Tuple[str, str, int, str]]:
    """Yield (topic, 'topic : comment', line number, door) from a FACTS.md file."""
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", ">", "-", "*")):
            continue
        parts = [part.strip() for part in stripped.split(" // ")]
        matched = re.match(r"^(.{2,160}?)\s+:\s+(.+)$", parts[0])
        if not matched:
            continue
        door = ""
        for part in parts[1:]:
            if part.startswith("door "):
                door = part[5:].strip()
        topic = matched.group(1).strip()
        yield topic, f"{topic} : {matched.group(2).strip()}", number, door


def link_doors(text: str) -> Iterator[Tuple[str, str]]:
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        urls = [cell for cell in cells if cell.startswith("http")]
        if urls and cells and not cells[0].startswith("-"):
            yield clean_markdown(cells[0]), urls[-1]


def thinker_entities(text: str) -> Iterator[Entity]:
    for line in text.splitlines():
        matched = re.match(r"^\|\s*\*\*(.+?)\*\*\s*\|(.+)$", line.strip())
        if not matched:
            continue
        label = clean_markdown(matched.group(1))
        cells = [clean_markdown(cell) for cell in matched.group(2).strip().strip("|").split("|")]
        wiki_match = re.search(r"https://en\.wikipedia\.org/wiki/([^)\s|]+)", line)
        wiki = urllib.parse.unquote(wiki_match.group(1)).replace("_", " ") if wiki_match else ""
        descr = " — ".join(cell for cell in cells[:2] if cell)
        name_words = words(label)
        aliases = [label]
        if len(name_words) >= 2 and len(name_words[-1]) >= 4:
            aliases.append(name_words[-1])
        if wiki and wiki != label:
            aliases.append(wiki)
        yield Entity(" ".join(name_words), label, descr, wiki, aliases, "stack:THINKERS.md", 500)


# ---------------------------------------------------------------- index


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, key TEXT UNIQUE, origin TEXT, url TEXT, title TEXT, tier TEXT, fetched REAL);
CREATE TABLE IF NOT EXISTS paras(id INTEGER PRIMARY KEY, doc INTEGER, heading TEXT, text TEXT, locator TEXT, door TEXT);
CREATE TABLE IF NOT EXISTS sents(id INTEGER PRIMARY KEY, para INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS post(term TEXT, sent INTEGER);
CREATE INDEX IF NOT EXISTS post_term ON post(term);
CREATE TABLE IF NOT EXISTS ppost(term TEXT, para INTEGER);
CREATE INDEX IF NOT EXISTS ppost_term ON ppost(term);
CREATE TABLE IF NOT EXISTS df(term TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS rel(a TEXT, b TEXT, kind TEXT, w REAL, PRIMARY KEY(a, b, kind));
CREATE TABLE IF NOT EXISTS entities(key TEXT PRIMARY KEY, label TEXT, descr TEXT, wiki TEXT, origin TEXT, fame INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS aliases(alias TEXT, entity TEXT, PRIMARY KEY(alias, entity));
CREATE TABLE IF NOT EXISTS doors(topic TEXT, url TEXT, PRIMARY KEY(topic, url));
CREATE TABLE IF NOT EXISTS lookups(q TEXT PRIMARY KEY, at REAL);
"""


@dataclass(frozen=True)
class Feature:
    term: str
    kind: str
    weight: float
    base: bool
    label: str
    root: str = ""


@dataclass
class Hit:
    sent: int
    score: float
    parts: Dict[str, float]
    text: str = ""
    paragraph: str = ""
    heading: str = ""
    locator: str = ""
    door: str = ""
    url: str = ""
    title: str = ""
    tier: str = ""
    origin: str = ""
    doc: int = 0
    anchor: float = 0.0
    covered: Set[str] = field(default_factory=set)
    filler: str = ""


class AliceIndex:
    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = Path(path) if path else Path(hydra_home()) / "alice_index.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(str(self.path), check_same_thread=False)
        self.con.executescript(SCHEMA)
        columns = {row[1] for row in self.con.execute("PRAGMA table_info(entities)")}
        if "fame" not in columns:
            self.con.execute("ALTER TABLE entities ADD COLUMN fame INTEGER DEFAULT 0")
        self._df_cache: Dict[str, int] = {}

    def close(self) -> None:
        self.con.close()

    # -- meta

    def _meta(self, key: str) -> Optional[str]:
        row = self.con.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (key, value))

    def sentence_count(self) -> int:
        return int(self.con.execute("SELECT COUNT(*) FROM sents").fetchone()[0])

    def stats(self) -> Dict[str, int]:
        rows = self.con.execute("SELECT origin, COUNT(*) FROM docs GROUP BY origin").fetchall()
        out = {f"docs_{origin}": count for origin, count in rows}
        out["sentences"] = self.sentence_count()
        out["paragraphs"] = int(self.con.execute("SELECT COUNT(*) FROM paras").fetchone()[0])
        out["entities"] = int(self.con.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
        out["relations"] = int(self.con.execute("SELECT COUNT(*) FROM rel").fetchone()[0])
        out["doors"] = int(self.con.execute("SELECT COUNT(*) FROM doors").fetchone()[0])
        return out

    # -- build

    def _signature(self) -> Tuple[str, List[Path], Optional[Path]]:
        root = stacks_root()
        digest = hashlib.sha256(INDEX_VERSION.encode())
        files: List[Path] = []
        if root is not None:
            files = _stack_files(root)
            for path in files:
                stat = path.stat()
                digest.update(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode())
        elif CARDS_PATH.is_file():
            stat = CARDS_PATH.stat()
            digest.update(f"{CARDS_PATH}|{stat.st_size}|{stat.st_mtime_ns}".encode())
        return digest.hexdigest(), files, root

    def ensure_built(self, force: bool = False) -> bool:
        with self.lock:
            signature, files, root = self._signature()
            if not force and self._meta("stack_signature") == signature:
                return False
            self.con.execute("PRAGMA synchronous=OFF")
            if self._meta("index_version") != INDEX_VERSION:
                for table in ("post", "ppost", "sents", "paras", "docs", "df", "entities", "aliases", "lookups", "rel"):
                    self.con.execute(f"DELETE FROM {table}")
                self._set_meta("index_version", INDEX_VERSION)
            self._drop_origin("stack")
            self.con.execute("DELETE FROM doors")
            self.con.execute("DELETE FROM aliases WHERE entity IN (SELECT key FROM entities WHERE origin LIKE 'stack:%')")
            self.con.execute("DELETE FROM entities WHERE origin LIKE 'stack:%'")
            if root is not None:
                for path in files:
                    self._ingest_file(root, path)
            else:
                self._ingest_cards()
            self._rebuild_df()
            self._set_meta("stack_signature", signature)
            self._set_meta("stack_root", str(root) if root else str(CARDS_PATH))
            self.con.commit()
            self.con.execute("PRAGMA synchronous=FULL")
            return True

    def _drop_origin(self, origin: str) -> None:
        docs = "SELECT id FROM docs WHERE origin = ?"
        paras = f"SELECT id FROM paras WHERE doc IN ({docs})"
        sents = f"SELECT id FROM sents WHERE para IN ({paras})"
        self.con.execute(f"DELETE FROM post WHERE sent IN ({sents})", (origin,))
        self.con.execute(f"DELETE FROM ppost WHERE para IN ({paras})", (origin,))
        self.con.execute(f"DELETE FROM sents WHERE para IN ({paras})", (origin,))
        self.con.execute(f"DELETE FROM paras WHERE doc IN ({docs})", (origin,))
        self.con.execute("DELETE FROM docs WHERE origin = ?", (origin,))

    def _ingest_file(self, root: Path, path: Path) -> None:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "LINK_INDEX.md":
            self.con.executemany("INSERT OR IGNORE INTO doors(topic, url) VALUES (?, ?)", list(link_doors(text)))
            return
        title_match = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.M)
        title = title_match.group(1) if title_match else rel
        doc = self._add_doc("stack:" + rel, "stack", "", title, "curated")
        units: List[Tuple[str, str, str, str, bool]] = []
        if path.name == "FACTS.md":
            for topic, unit, number, door in facts_units(text):
                units.append((topic, unit, f"stacks/{rel}:{number}", door, True))
                if door:
                    self.con.execute("INSERT OR IGNORE INTO doors(topic, url) VALUES (?, ?)", (topic, door))
        else:
            for heading, paragraph, number, single in markdown_units(text):
                units.append((heading, paragraph, f"stacks/{rel}:{number}", "", single))
        if path.name == "THINKERS.md":
            for entity in thinker_entities(text):
                self.add_entity(entity)
        self._add_units(doc, units, bulk=True)

    def _ingest_cards(self) -> None:
        if not CARDS_PATH.is_file():
            return
        shelves: Dict[str, List[Tuple[str, str, str, str, bool]]] = defaultdict(list)
        titles: Dict[str, str] = {}
        for line in CARDS_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            card = json.loads(line)
            slug = card.get("slug") or "stack"
            titles[slug] = card.get("title") or slug
            unit = f"{card['topic']} : {card['comment']}"
            shelves[slug].append((card["topic"], unit, card.get("source") or slug, card.get("door") or "", True))
        for slug, units in shelves.items():
            doc = self._add_doc(f"stack:cards/{slug}", "stack", "", titles[slug], "curated")
            self._add_units(doc, units, bulk=True)

    def _add_doc(self, key: str, origin: str, url: str, title: str, tier: str) -> int:
        cur = self.con.execute(
            "INSERT INTO docs(key, origin, url, title, tier, fetched) VALUES (?, ?, ?, ?, ?, ?)",
            (key, origin, url, title, tier, time.time()),
        )
        return int(cur.lastrowid)

    def _add_units(self, doc: int, units: Sequence[Tuple[str, str, str, str, bool]], bulk: bool) -> int:
        post_rows: List[Tuple[str, int]] = []
        ppost_rows: List[Tuple[str, int]] = []
        added: Dict[str, int] = defaultdict(int)
        count = 0
        for heading, paragraph, locator, door, single in units:
            cur = self.con.execute(
                "INSERT INTO paras(doc, heading, text, locator, door) VALUES (?, ?, ?, ?, ?)",
                (doc, heading, paragraph, locator, door),
            )
            para = int(cur.lastrowid)
            para_terms = set(content(paragraph))
            ppost_rows.extend((term, para) for term in para_terms)
            ppost_rows.extend(("h:" + term, para) for term in set(content(heading)))
            for sentence in ([paragraph] if single else sentences(paragraph)):
                cur = self.con.execute("INSERT INTO sents(para, text) VALUES (?, ?)", (para, sentence[:1200]))
                sent = int(cur.lastrowid)
                terms = content(sentence)
                keys = set(terms) | set(bigrams(terms))
                post_rows.extend((term, sent) for term in keys)
                for term in keys:
                    added[term] += 1
                count += 1
        self.con.executemany("INSERT INTO post(term, sent) VALUES (?, ?)", post_rows)
        self.con.executemany("INSERT INTO ppost(term, para) VALUES (?, ?)", ppost_rows)
        if not bulk:
            self.con.executemany(
                "INSERT INTO df(term, n) VALUES (?, ?) ON CONFLICT(term) DO UPDATE SET n = n + excluded.n",
                list(added.items()),
            )
            self._df_cache.clear()
        return count

    def _rebuild_df(self) -> None:
        self.con.execute("DELETE FROM df")
        self.con.execute("INSERT INTO df(term, n) SELECT term, COUNT(*) FROM post GROUP BY term")
        self._df_cache.clear()

    def has_page(self, url: str) -> bool:
        row = self.con.execute("SELECT fetched FROM docs WHERE key = ?", ("web:" + url,)).fetchone()
        return bool(row and time.time() - float(row[0] or 0) < PAGE_TTL)

    def add_page(self, page: Page) -> int:
        with self.lock:
            key = "web:" + page.url
            old = self.con.execute("SELECT id FROM docs WHERE key = ?", (key,)).fetchone()
            if old:
                self._drop_doc(int(old[0]))
            doc = self._add_doc(key, "web", page.url, page.title, page.tier)
            units = [(heading, text, page.url, page.url, page.single) for heading, text in page.paragraphs]
            count = self._add_units(doc, units, bulk=False)
            self.con.commit()
            return count

    def _drop_doc(self, doc: int) -> None:
        paras = "SELECT id FROM paras WHERE doc = ?"
        sents = f"SELECT id FROM sents WHERE para IN ({paras})"
        rows = self.con.execute(f"SELECT term, COUNT(*) FROM post WHERE sent IN ({sents}) GROUP BY term", (doc,)).fetchall()
        self.con.executemany("UPDATE df SET n = n - ? WHERE term = ?", [(n, term) for term, n in rows])
        self.con.execute(f"DELETE FROM post WHERE sent IN ({sents})", (doc,))
        self.con.execute(f"DELETE FROM ppost WHERE para IN ({paras})", (doc,))
        self.con.execute(f"DELETE FROM sents WHERE para IN ({paras})", (doc,))
        self.con.execute("DELETE FROM paras WHERE doc = ?", (doc,))
        self.con.execute("DELETE FROM docs WHERE id = ?", (doc,))
        self._df_cache.clear()

    # -- entities and relations

    def add_entity(self, entity: Entity) -> None:
        with self.lock:
            self.con.execute(
                "INSERT OR REPLACE INTO entities(key, label, descr, wiki, origin, fame) VALUES (?, ?, ?, ?, ?, ?)",
                (entity.key, entity.label, entity.descr, entity.wiki, entity.origin, entity.fame),
            )
            names = {entity.label, *entity.aliases}
            rows = [(" ".join(words(name)), entity.key) for name in names if words(name)]
            self.con.executemany("INSERT OR IGNORE INTO aliases(alias, entity) VALUES (?, ?)", rows)
            self.con.commit()

    def entity(self, key: str) -> Optional[Entity]:
        row = self.con.execute("SELECT key, label, descr, wiki, origin, fame FROM entities WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        aliases = [r[0] for r in self.con.execute("SELECT alias FROM aliases WHERE entity = ?", (key,))]
        return Entity(row[0], row[1], row[2] or "", row[3] or "", aliases, row[4] or "", int(row[5] or 0))

    def alias_lookup(self, phrase: str) -> List[Entity]:
        keys = [r[0] for r in self.con.execute("SELECT entity FROM aliases WHERE alias = ?", (phrase,))]
        found = [entity for entity in (self.entity(key) for key in keys) if entity]
        return sorted(found, key=lambda entity: -entity.fame)

    def relations(self, key: str) -> List[Tuple[str, str, float]]:
        return [(b, kind, float(w)) for b, kind, w in self.con.execute("SELECT b, kind, w FROM rel WHERE a = ?", (key,))]

    def add_relation(self, a: str, b: str, kind: str, weight: float) -> None:
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO rel(a, b, kind, w) VALUES (?, ?, ?, ?)", (a, b, kind, weight))
            self.con.commit()

    def looked_up(self, query: str) -> bool:
        row = self.con.execute("SELECT at FROM lookups WHERE q = ?", (query,)).fetchone()
        return bool(row and time.time() - float(row[0]) < LOOKUP_TTL)

    def mark_lookup(self, query: str) -> None:
        with self.lock:
            self.con.execute("INSERT OR REPLACE INTO lookups(q, at) VALUES (?, ?)", (query, time.time()))
            self.con.commit()

    # -- weights

    def df(self, term: str) -> int:
        if term not in self._df_cache:
            row = self.con.execute("SELECT n FROM df WHERE term = ?", (term,)).fetchone()
            self._df_cache[term] = int(row[0]) if row else 0
        return self._df_cache[term]

    def rarity(self, term: str) -> float:
        total = max(1, self.sentence_count())
        value = 1.0 + math.log((total + 1) / (self.df(term) + 1))
        return round(min(RARITY_MAX, max(RARITY_MIN, value)), 3)

    # -- search

    def search(self, features: Sequence[Feature], cue: Optional[str], focus: Optional[Feature], limit: int = 8,
               slot: Optional["Slot"] = None, subjects: Optional[Set[frozenset]] = None) -> List[Hit]:
        with self.lock:
            return self._search(features, cue, focus, limit, slot, subjects or set())

    def _search(self, features: Sequence[Feature], cue: Optional[str], focus: Optional[Feature], limit: int,
                slot: Optional["Slot"], subjects: Set[frozenset]) -> List[Hit]:
        sent_sets: List[Set[int]] = []
        for feature in features:
            rows = self.con.execute("SELECT sent FROM post WHERE term = ?", (feature.term,)).fetchall()
            sent_sets.append({row[0] for row in rows})
        candidates: Set[int] = set().union(*sent_sets) if sent_sets else set()
        if not candidates:
            return []
        para_of: Dict[int, int] = {}
        ids = list(candidates)
        for start in range(0, len(ids), 900):
            chunk = ids[start:start + 900]
            marks = ",".join("?" * len(chunk))
            for sent, para in self.con.execute(f"SELECT id, para FROM sents WHERE id IN ({marks})", chunk):
                para_of[sent] = para
        unigram = [i for i, f in enumerate(features) if " " not in f.term]
        para_sets: Dict[int, Set[int]] = {}
        head_sets: Dict[int, Set[int]] = {}
        for i in unigram:
            term = features[i].term
            para_sets[i] = {r[0] for r in self.con.execute("SELECT para FROM ppost WHERE term = ?", (term,))}
            head_sets[i] = {r[0] for r in self.con.execute("SELECT para FROM ppost WHERE term = ?", ("h:" + term,))}

        scored: List[Tuple[float, int, Dict[str, float], Set[str]]] = []
        for sent in candidates:
            para = para_of.get(sent)
            parts: Dict[str, float] = {}
            covered: Set[str] = set()
            used: Dict[str, float] = defaultdict(float)

            def credit(name: str, feature: Feature, gain: float) -> None:
                group = KIND_GROUP.get(feature.kind)
                if group is not None:
                    gain = min(gain, GROUP_CAP[group] - used[group])
                    used[group] += max(0.0, gain)
                if gain > 0:
                    parts[name] = round(gain, 3)
                    if feature.root:
                        covered.add(feature.root)

            for i, feature in enumerate(features):
                if sent in sent_sets[i]:
                    credit(f"{feature.label} [{feature.kind}]", feature, feature.weight)
                elif i in para_sets and para in para_sets[i] and feature.kind in ("term", "learned"):
                    credit(f"{feature.label} [context]", feature, feature.weight + W_CONTEXT)
                if i in head_sets and para in head_sets[i]:
                    parts[f"{feature.label} [heading]"] = W_HEADING
            scored.append((sum(parts.values()), sent, parts, covered))
        scored.sort(key=lambda item: -item[0])
        top = scored[:FINAL_CANDIDATES]

        hits: List[Hit] = []
        marks = ",".join("?" * len(top))
        rows = self.con.execute(
            "SELECT s.id, s.text, p.text, p.heading, p.locator, p.door, d.url, d.title, d.tier, d.origin, d.id, p.id "
            f"FROM sents s JOIN paras p ON s.para = p.id JOIN docs d ON p.doc = d.id WHERE s.id IN ({marks})",
            [sent for _, sent, _, _ in top],
        ).fetchall()
        info = {row[0]: row for row in rows}
        web_docs = sorted({row[10] for row in rows if row[9] == "web"})
        first_para: Dict[int, int] = {}
        if web_docs:
            doc_marks = ",".join("?" * len(web_docs))
            first_para = dict(self.con.execute(
                f"SELECT doc, MIN(id) FROM paras WHERE doc IN ({doc_marks}) GROUP BY doc", web_docs).fetchall())
        cue_re = CUE_PATTERNS.get(cue or "")
        anchor_names: Set[str] = set()
        for feature in features:
            if feature.base:
                for kind in (feature.kind, "context", "coref", "lifespan", "about"):
                    anchor_names.add(f"{feature.label} [{kind}]")
        if focus is not None:
            anchor_names.add("definition [define]")
        if slot is not None:
            anchor_names.add(f"slot [{slot.kind}]")
        for _score, sent, parts, covered in top:
            row = info.get(sent)
            if not row:
                continue
            parts = dict(parts)
            covered = set(covered)
            text = row[1]
            lead = row[10] in first_para and row[11] - first_para[row[10]] < LEAD_PARAS
            if PRONOUN_START.match(text) or lead:
                heads = {name[: -len(" [heading]")] for name in parts if name.endswith(" [heading]")}
                for name in list(parts):
                    if name.endswith(" [context]") and name[: -len(" [context]")] in heads:
                        parts[name[: -len(" [context]")] + " [coref]"] = -W_CONTEXT
                title_terms = set(content(row[7] or ""))
                named = sum(value for name, value in parts.items() if name.endswith(("[entity]", "[alias]")))
                for feature in features:
                    if (feature.kind == "entity" and f"{feature.label} [entity]" not in parts
                            and set(feature.term.split()) == title_terms):
                        gain = min(feature.weight, GROUP_CAP["name"] - named)
                        named += max(0.0, gain)
                        if gain > 0:
                            parts[f"{feature.label} [about]"] = round(gain, 3)
            if cue != "where" and LIFESPAN.search(text):
                for feature in features:
                    if feature.term in LIFE_TERMS and feature.base and f"{feature.label} [{feature.kind}]" not in parts:
                        parts.pop(f"{feature.label} [context]", None)
                        parts[f"{feature.label} [lifespan]"] = feature.weight
                        covered.add(feature.root or feature.term)
            if cue_re and cue_re.search(text):
                parts[f"cue {cue}"] = CUE_WEIGHT[cue]
            if cue == "where" and STRONG_WHERE.search(text):
                parts["cue where"] = W_STRONG_WHERE
            if focus is not None and _defines(text, focus.term):
                parts["definition [define]"] = focus.weight
            filled = _fills(text, slot) if slot is not None else ""
            if filled:
                parts[f"slot [{slot.kind}]"] = W_SLOT
            bonus = TIER_BONUS.get(row[8] or "", 0.0)
            if bonus:
                parts[f"tier {row[8]}"] = bonus
            if lead:
                parts["lead paragraph"] = W_LEAD
            if subjects and frozenset(content(row[7] or "")) in subjects:
                parts["subject page"] = W_SUBJECT
            anchor = round(sum(value for name, value in parts.items() if name in anchor_names), 3)
            hits.append(Hit(sent, round(sum(parts.values()), 3), parts, text, row[2], row[3] or "", row[4] or "",
                            row[5] or "", row[6] or "", row[7] or "", row[8] or "", row[9] or "", row[10], anchor, covered,
                            fold(filled)))
        _vote(hits)
        hits.sort(key=lambda hit: (-hit.score, hit.sent))
        return hits[:limit]


@dataclass(frozen=True)
class Slot:
    """The answer position a question asks for: the X in 'X is the capital of Y' or 'painted by X'."""
    kind: str
    word: str
    forms: Tuple[str, ...]
    exclude: frozenset
    required: bool = False
    obj: frozenset = frozenset()


def question_slot(question: str) -> Optional[Slot]:
    text = strip_question(question).strip().rstrip("?")
    exclude = frozenset(content(text))
    counted = COUNT_RE.match(text)
    if counted:
        word = stem(fold(counted.group("noun")))
        return Slot("count", word, _forms(word), exclude, True)
    relational = RELATIONAL_RE.match(text)
    if relational:
        head = content(relational.group("head"))
        if head:
            word = head[-1]
            obj = frozenset(content(relational.group("obj")))
            return Slot("relation", word, _forms(word), exclude, word in RELATION_NOUNS, obj)
    agent = AGENT_RE.match(text)
    if agent and agent.group("verb").lower() not in AUXILIARY:
        word = stem(fold(agent.group("verb")))
        return Slot("agent", word, _forms(word), exclude)
    return None


def _forms(word: str) -> Tuple[str, ...]:
    irregular = [key for key, value in LEMMA.items() if value == word]
    return tuple(dict.fromkeys([word, *irregular]))


def _fills(text: str, slot: Slot) -> str:
    """The value filling the answer slot, or an empty string."""
    forms = "|".join(re.escape(form) for form in slot.forms)
    word = rf"(?:{forms})[a-z]*"
    filler = r"[\w.'’-]{2,}"
    if slot.kind == "count":
        number = re.search(rf"(?i:\b(\d[\d,.]*)\s+(?:[a-z-]+\s+)?{word}\b)", text)
        return number.group(1).rstrip(".,") if number else ""
    if slot.kind == "relation":
        if slot.obj and not _relation_object(text, word, slot.obj):
            return ""
        patterns = [rf"(?i:\b({filler})\s+{COPULA}\s+(?:[\w'’\"“-]+\s+){{0,4}}?[\"“]?{word}\b)"]
        if slot.required:
            patterns.append(
                rf"(?i:\b{word}\b)(?:\s+(?:city|of|the|[A-Z][\w.'’-]*)){{0,5}}\s*(?i:{COPULA}|,|:)\s+(?:the\s+)?([A-Z][\w'’-]+)"
            )
    else:
        past = "|".join(re.escape(form) for form in slot.forms[1:]) or "(?!)"
        acted = rf"(?:{re.escape(slot.word)}e?d|{past})"
        patterns = [
            rf"(?i:\b(?:{word}|{WORK_NOUNS})\b(?:\s+[\w,'’-]+){{0,6}}?\s+by\s+(?:the\s+)?(?:[a-z]+\s+){{0,3}})([A-Z][\w'’-]+)",
            rf"\b([A-Z][\w'’-]+(?:\s+(?:da|de|von|van)?\s*[A-Z][\w'’-]+)*)\s+(?:[a-z]+\s+){{0,2}}?(?i:{acted})\b",
        ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            name = match.group(1)
            if fold(name) not in STOP and stem(fold(name.split()[-1])) not in slot.exclude:
                return name.strip(".,")
    return ""


def _vote(hits: List[Hit]) -> None:
    """Each other distinct sentence that fills the slot with the same value adds W_VOTE, up to VOTE_CAP."""
    texts: Dict[str, Set[str]] = defaultdict(set)
    for hit in hits:
        if hit.filler:
            texts[hit.filler].add(fold(hit.text)[:140])
    for hit in hits:
        others = len(texts.get(hit.filler, ())) - 1 if hit.filler else 0
        if others > 0:
            gain = min(VOTE_CAP, W_VOTE * others)
            hit.parts[f"votes {hit.filler}"] = gain
            hit.score = round(hit.score + gain, 3)
            hit.anchor = round(hit.anchor + gain, 3)


def _relation_object(text: str, word: str, obj: frozenset) -> bool:
    """'capital of the state of Texas' answers for Texas; an 'of' phrase after the head must name the asked object."""
    need = len(obj) if len(obj) <= 2 else math.ceil(len(obj) * 2 / 3)
    for match in re.finditer(rf"(?i:\b{word}\b)\s+of\s+((?:[\w.'’-]+\s*){{1,6}})", text):
        return len(set(content(match.group(1))) & obj) >= need
    return True


def _defines(text: str, focus_term: str) -> bool:
    """The focus term opens the sentence and a defining marker follows it closely."""
    for match in re.finditer(r"[A-Za-z0-9\u00C0-\u024F]+", text[:90]):
        if stem(fold(match.group(0))) == focus_term:
            tail = text[match.end(): match.end() + 260]
            if LIFESPAN.match(tail.lstrip()):
                return True
            tail = re.sub(r"\([^()]*\)", " ", tail)
            return bool(DEFINE_MARK.search(tail[:60]))
    return False


# ---------------------------------------------------------------- recognition


def _caps_spans(text: str) -> List[str]:
    connector = r"(?:\s+(?:of|de|da|del|della|von|van|der|the|la|le|du|bin|ibn|al|y)\s+|\s+)"
    pattern = re.compile(r"\b([A-Z][\w'’.-]*(?:" + connector + r"[A-Z][\w'’.-]*)*)")
    spans = []
    for match in pattern.finditer(text):
        span = match.group(1).strip(" .")
        first = span.split()[0].lower()
        if first in STOP and len(span.split()) == 1:
            continue
        if first in STOP:
            span = " ".join(span.split()[1:])
        if span:
            spans.append(span)
    return spans


def recognize(index: AliceIndex, question: str, extra: Sequence[str], web: bool, cue: Optional[str]) -> Tuple[List[Entity], List[Entity]]:
    """Return (chosen entities, rival readings of the first one)."""
    text = " ".join([question, *extra])
    focus = focus_words(strip_question(question)) + [w for reply in extra for w in focus_words(reply)]
    context_terms = set(content(text))
    spans = [" ".join(words(span)) for span in _caps_spans(text)]
    grams: List[str] = []
    for size in (3, 2, 1):
        for start in range(0, len(focus) - size + 1):
            grams.append(" ".join(focus[start:start + size]))
    stripped = strip_question(question).strip()
    named = DEFINIENDUM_RE.match(stripped)
    agent = AGENT_RE.match(stripped.rstrip("?"))
    if named:
        phrase = words(named.group(1))
    elif agent and agent.group("verb").lower() not in AUXILIARY:
        phrase = words(re.sub(r"^(the|a|an)\s+", "", agent.group("obj").strip(), flags=re.I))
    else:
        phrase = []
    whole = [" ".join(phrase)] if 2 <= len(phrase) <= 5 and phrase[0] not in STOP else []
    candidates = [c for c in dict.fromkeys(spans + whole + grams) if c and not c.isdigit()]

    chosen: List[Entity] = []
    covered: Set[str] = set()
    for cand in candidates:
        if set(cand.split()) <= covered:
            continue
        found = index.alias_lookup(cand)
        if found:
            chosen.append(_prefer([(cand, entity) for entity in found], context_terms)[0])
            covered |= set(cand.split())
    span_words = max((len(span.split()) for span in spans), default=0)
    if not web or (chosen and len(covered) >= span_words):
        return chosen[:2], []

    pool: List[Tuple[str, Entity]] = []
    seen: Set[str] = set()
    multi = [c for c in candidates if len(c.split()) > 1]
    singles = sorted((c for c in candidates if len(c.split()) == 1), key=lambda c: -index.rarity(stem(c)))
    ordered = (multi + singles)[:5]
    framed = PERSON_FRAME.match(strip_question(question).strip())
    subject = next((g for g in framed.groups() if g), "") if framed else ""
    if re.match(r"^\s*(the|a|an)\b", subject, re.I):
        subject = ""
    person_words = focus_words(subject)
    if person_words and all(index.df(stem(word)) == 0 for word in person_words):
        person_words = [w for reply in extra for w in focus_words(reply)] or person_words
    person_multi = [" ".join(person_words[i:i + n]) for n in (3, 2) for i in range(0, len(person_words) - n + 1)]
    person_single = sorted(dict.fromkeys(person_words), key=lambda w: -index.rarity(stem(w)))
    person_cands = list(dict.fromkeys(spans + person_multi[:2] + person_single))[:6]
    def human(query: str) -> List[Tuple[str, float]]:
        return [(qid, 0.0) for qid in wd_human_search(query)]

    plan: List[Tuple[Callable[[str], List[Tuple[str, float]]], List[str]]] = []
    if person_cands and cue != "where":
        plan.append((human, person_cands))
    plan.append((wd_label_matches, ordered))
    bonus: Dict[str, float] = {}
    for search, cands in plan:
        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(search, cands))
        by_cand = [(cand, matches[:5]) for cand, matches in zip(cands, results)]
        wanted_ids = list(dict.fromkeys(qid for _, matches in by_cand for qid, _ in matches))[:40]
        found = {entity.key: entity for entity in wd_entities(wanted_ids)}
        for cand, matches in by_cand:
            for qid, match_bonus in matches:
                entity = found.get(qid)
                if not entity or qid in seen:
                    continue
                if search is human and not _names(entity, cand, cand in spans):
                    continue
                if search is wd_label_matches and entity.fame < MIN_CONCEPT_FAME:
                    continue
                seen.add(qid)
                bonus[qid] = match_bonus
                pool.append((cand, entity))
        if pool:
            break
    if not pool:
        return chosen[:2], []
    top = _prefer(pool, context_terms, bonus)[0]
    top_cand = next(cand for cand, entity in pool if entity.key == top.key)
    rivals: List[Entity] = []
    if _overlap(top, top_cand, context_terms) == 0:
        rivals = [entity for cand, entity in pool
                  if cand == top_cand and entity.key != top.key and entity.descr and entity.fame * 3 >= top.fame][:4]
    index.add_entity(top)
    _index_description(index, top)
    return ([top] + chosen)[:2], rivals


def _names(entity: Entity, cand: str, capitalized: bool) -> bool:
    """A lowercase phrase must sit inside the label; a capitalized span may also match an alias."""
    wanted = set(cand.split())
    names = [entity.label, *entity.aliases] if capitalized and len(wanted) > 1 else [entity.label]
    return any(wanted <= set(words(name)) for name in names)


def _overlap(entity: Entity, cand: str, terms: Set[str]) -> int:
    """Context terms the description shares, after removing the name itself."""
    own = set(content(cand)) | set(content(entity.label))
    return len(set(content(entity.descr)) & (terms - own))


def _prefer(pairs: Sequence[Tuple[str, Entity]], terms: Set[str], bonus: Optional[Dict[str, float]] = None) -> List[Entity]:
    """Rank readings: query words the name covers, shared context terms, then log2 fame plus match bonus."""
    bonus = bonus or {}

    def rank(item: Tuple[int, Tuple[str, Entity]]) -> Tuple[int, int, float, int]:
        position, (cand, entity) = item
        standing = math.log2(1 + entity.fame) + bonus.get(entity.key, 0.0)
        return (-len(cand.split()), -_overlap(entity, cand, terms), -standing, position)
    return [entity for _, (_, entity) in sorted(enumerate(pairs), key=rank)]


def _index_description(index: AliceIndex, entity: Entity) -> None:
    if not entity.descr:
        return
    url = "https://www.wikidata.org/wiki/" + entity.key
    if index.has_page(url):
        return
    index.add_page(Page(url, entity.label, "curated", [(entity.label, f"{entity.label} — {entity.descr}.")], single=True))


# ---------------------------------------------------------------- features


def strip_question(text: str) -> str:
    return re.sub(r"^\s*(please\s+)?(tell me about|tell me|give me|what do you know about)\s+", "", text, flags=re.I)


def build_features(index: AliceIndex, question: str, extra: Sequence[str], entities: Sequence[Entity]) -> Tuple[List[Feature], Optional[Feature]]:
    feats: Dict[str, Feature] = {}

    def add(term: str, kind: str, weight: float, base: bool, label: str, root: str = "") -> None:
        if not term:
            return
        old = feats.get(term)
        if old is None:
            feats[term] = Feature(term, kind, round(weight, 3), base, label, root)
            return
        keep_root = old.root or root
        if weight > old.weight:
            feats[term] = Feature(term, kind, round(weight, 3), base or old.base, old.label, keep_root)
        elif (base and not old.base) or keep_root != old.root:
            feats[term] = Feature(term, old.kind, old.weight, base or old.base, old.label, keep_root)

    q_terms = content(strip_question(question))
    for term in dict.fromkeys(q_terms):
        add(term, "term", index.rarity(term), True, term, term)
    for gram in bigrams(q_terms):
        add(gram, "phrase", W_PHRASE, True, gram)
    for quoted in re.findall(r"[\"“]([^\"”]{2,120})[\"”]", question):
        for gram in bigrams(content(quoted)):
            add(gram, "quoted", W_QUOTED, True, gram)

    for term in dict.fromkeys(q_terms):
        for variant in derived_forms(index, term):
            if variant not in q_terms:
                add(variant, "derived", W_DERIVED, False, f"{variant}<{term}", term)

    for token in words(question):
        expansion = SYNONYMS.get(token)
        if expansion:
            exp_terms = content(expansion)
            for term in exp_terms:
                add(term, "alias", W_ALIAS, False, term)
            for gram in bigrams(exp_terms):
                add(gram, "alias", W_ALIAS, False, gram)

    for entity in entities:
        label_terms = content(entity.label)
        in_query = bool(label_terms) and all(term in q_terms for term in label_terms)
        for gram in bigrams(label_terms)[:2]:
            add(gram, "entity", W_ENTITY, in_query, gram)
        for alias in entity.aliases[:8]:
            alias_terms = content(alias)
            if len(alias_terms) == 1:
                term = alias_terms[0]
                if term not in q_terms and len(term) >= 4 and index.rarity(term) >= 2.0:
                    add(term, "alias", W_ALIAS, False, term)
            for gram in bigrams(alias_terms)[:2]:
                add(gram, "alias", W_ALIAS, False, gram)
        for term in [t for t in content(entity.descr) if not t.isdigit() and t not in q_terms][:6]:
            add(term, "related", W_RELATED, False, term)

    for reply in extra:
        reply_terms = content(reply)
        for term in dict.fromkeys(reply_terms):
            add(term, "learned", index.rarity(term) + W_LEARNED, True, term, term)
        for gram in bigrams(reply_terms):
            add(gram, "phrase", W_PHRASE, True, gram)

    for term, kind, weight in index.relations(focus_key(question)):
        add(term, kind, weight, False, term)

    focus: Optional[Feature] = None
    relational = RELATIONAL_RE.match(strip_question(question).strip())
    relation_head = content(relational.group("head"))[-1:] if relational else []
    if DEFINITIONAL_RE.match(question.strip()) and not (relation_head and relation_head[0] in RELATION_NOUNS):
        unigrams = [f for f in feats.values() if f.base and " " not in f.term and f.kind == "term"]
        if unigrams:
            rarest = max(unigrams, key=lambda f: f.weight)
            focus = Feature(rarest.term, "define", rarest.weight, True, "definition")
    return list(feats.values()), focus


def derived_forms(index: AliceIndex, term: str) -> List[str]:
    """Morphological family and listed semantic neighbours that occur in the index."""
    if " " in term or term.isdigit() or len(term) < 4:
        return list(SEMANTIC.get(term, []))
    bases = {term}
    for suffix in DERIVE_SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            bases.add(term[: -len(suffix)])
    if term.endswith("e"):
        bases.add(term[:-1])
    found: List[str] = []
    for base in bases:
        for variant in [base, *(base + suffix for suffix in DERIVE_SUFFIXES)]:
            if variant != term and variant not in found and index.df(variant) > 0:
                found.append(variant)
    for neighbour in SEMANTIC.get(term, []):
        if neighbour not in found:
            found.append(neighbour)
    return found[:6]


def drop_unknown(index: AliceIndex, features: List[Feature], focus: Optional[Feature], replied: bool) -> Tuple[List[Feature], Optional[Feature]]:
    """After a reply, question terms the index never saw leave the mass; the reply stands in for them."""
    if not replied:
        return features, focus
    unknown = {f.term for f in features if f.kind == "term" and index.df(f.term) == 0}
    if not unknown:
        return features, focus
    kept = [f for f in features
            if not (f.base and f.kind in ("term", "phrase", "quoted", "entity") and set(f.term.split()) & unknown)]
    if focus is not None and focus.term in unknown:
        focus = None
    return kept, focus


def focus_key(question: str) -> str:
    return " ".join(sorted(set(content(strip_question(question)))))


def query_mass(features: Sequence[Feature], focus: Optional[Feature], cue: Optional[str], slot: Optional[Slot] = None) -> float:
    total = sum(f.weight for f in features if f.base)
    if focus is not None:
        total += focus.weight
    if slot is not None:
        total += W_SLOT
    if cue in CUE_IN_MASS:
        total += CUE_WEIGHT[cue]
    return round(total, 3)


# ---------------------------------------------------------------- harvest


def harvest(index: AliceIndex, question: str, extra: Sequence[str], entities: Sequence[Entity], cue: Optional[str]) -> List[str]:
    """Fetch whitelisted pages for the question, index them, and return their URLs."""
    focus = " ".join(focus_words(strip_question(question)) + [w for reply in extra for w in focus_words(reply)])
    if not focus:
        return []
    natural = re.sub(r"^(who|whom|whose|what|when|where|which|why|how)\s+(is|was|are|were|did|does|do)?\s*", "",
                     strip_question(question).strip().rstrip("?"), flags=re.I)
    natural = " ".join([natural, *extra]).strip()
    titles: List[str] = []
    for entity in entities[:2]:
        if entity.wiki:
            titles.append(entity.wiki)
    person = any(entity.person for entity in entities)
    tech = bool(set(words(focus)) & TECH_WORDS)
    urls: List[str] = []
    searched = index.looked_up(focus)
    with ThreadPoolExecutor(max_workers=4) as pool:
        if not searched:
            wp_jobs = [pool.submit(wp_search, natural, 2)] if natural and natural != focus else []
            wp_jobs.append(pool.submit(wp_search, focus, 2))
            sep_job = pool.submit(sep_search, focus, 1) if not person and cue not in ("when", "where", "count") else None
            mdn_job = pool.submit(mdn_search, focus, 1) if tech else None
            for job in wp_jobs:
                for title in job.result():
                    if title not in titles:
                        titles.append(title)
            if sep_job is not None:
                urls.extend(sep_job.result())
            if mdn_job is not None:
                urls.extend(mdn_job.result())
            index.mark_lookup(focus)
        jobs = []
        for title in titles[:4]:
            if not index.has_page(wiki_url(title)):
                jobs.append(pool.submit(wp_page, title))
        for url in urls:
            if not index.has_page(url):
                jobs.append(pool.submit(html_page, url))
        pages = [job.result() for job in jobs]
    added = []
    for page in pages:
        if page and page.paragraphs:
            index.add_page(page)
            added.append(page.url)
    return added


# ---------------------------------------------------------------- answer


@dataclass
class Pending:
    question: str
    extra: List[str]
    rounds: int
    at: float


@dataclass
class Retrieval:
    action: str
    text: str
    route: str
    hits: List[Hit] = field(default_factory=list)
    mass: float = 0.0
    threshold: float = 0.0
    fetched: List[str] = field(default_factory=list)


_PENDING: Dict[str, Pending] = {}
_DEFAULT: Optional[AliceIndex] = None
_DEFAULT_LOCK = threading.Lock()


def default_index() -> AliceIndex:
    global _DEFAULT
    with _DEFAULT_LOCK:
        wanted = Path(hydra_home()) / "alice_index.db"
        if _DEFAULT is None or _DEFAULT.path != wanted:
            _DEFAULT = AliceIndex(wanted)
        return _DEFAULT


def has_pending(session: str = "default") -> bool:
    pending = _PENDING.get(session)
    if pending and time.time() - pending.at > PENDING_TTL:
        _PENDING.pop(session, None)
        return False
    return pending is not None


def clear_pending(session: str = "default") -> None:
    _PENDING.pop(session, None)


def _is_reply(prompt: str) -> bool:
    text = prompt.strip()
    if not text or CODE_RE.search(text) or len(text.split()) > 12:
        return False
    if re.match(r"^\s*(never ?mind|new question|forget it|cancel)\b", text, re.I):
        return False
    if INQUIRY_RE.match(text) and len(focus_words(text)) >= 2:
        return False
    return True


def is_followup(prompt: str, session: str = "default") -> bool:
    return has_pending(session) and _is_reply(prompt)


def web_enabled(web: Optional[bool]) -> bool:
    if web is not None:
        return web
    return os.environ.get("ALICE_WEB", "1").strip() not in ("0", "off", "false", "no")


def threshold_for(mass: float) -> float:
    return round(max(FLOOR, THRESHOLD * mass), 3)


def question_roots(features: Sequence[Feature]) -> Dict[str, float]:
    return {f.root: f.weight for f in features if f.base and f.kind == "term" and f.root}


def reply_roots(features: Sequence[Feature]) -> Dict[str, float]:
    return {f.root: f.weight for f in features if f.base and f.kind == "learned" and f.root}


def covered_share(hit: Hit, roots: Dict[str, float]) -> float:
    total = sum(roots.values())
    if total <= 0:
        return 1.0
    return sum(weight for root, weight in roots.items() if root in hit.covered) / total


def _passing(hits: Sequence[Hit], need: float, features: Sequence[Feature], cue: Optional[str] = None,
             slot: Optional[Slot] = None) -> List[Hit]:
    """Sentences at or above threshold whose anchor, term coverage, and answer type also hold."""
    roots = question_roots(features)
    replies = reply_roots(features)
    out: List[Hit] = []
    seen: Set[str] = set()
    per_doc: Dict[int, int] = defaultdict(int)
    required = set(roots) & MANDATORY
    for hit in hits:
        if hit.score < need:
            break
        if hit.anchor < max(FLOOR, ANCHOR_SHARE * need):
            continue
        asked = covered_share(hit, roots) >= COVER_SHARE and required <= hit.covered
        told = bool(replies) and covered_share(hit, replies) >= COVER_SHARE
        if not (asked or told):
            continue
        if cue in REQUIRED_CUES and f"cue {cue}" not in hit.parts:
            continue
        if slot is not None and slot.required and f"slot [{slot.kind}]" not in hit.parts:
            continue
        key = fold(hit.text)[:140]
        if key in seen or per_doc[hit.doc] >= 2:
            continue
        seen.add(key)
        per_doc[hit.doc] += 1
        out.append(hit)
        if len(out) >= MAX_ANSWERS:
            break
    return out


def answer(prompt: str, *, session: str = "default", web: Optional[bool] = None, allow_ask: bool = True,
           index: Optional[AliceIndex] = None) -> Retrieval:
    index = index or default_index()
    index.ensure_built()
    use_web = web_enabled(web)
    pending = _PENDING.get(session) if has_pending(session) else None
    if pending and _is_reply(prompt):
        question, extra, rounds = pending.question, pending.extra + [prompt.strip()], pending.rounds
    else:
        question, extra, rounds = prompt.strip(), [], 0
    _PENDING.pop(session, None)

    cue = answer_cue(question)
    slot = question_slot(question)
    entities, rivals = recognize(index, question, extra, use_web, cue)
    subjects = {frozenset(content(entity.label)) for entity in entities if content(entity.label)}
    fetched: List[str] = []

    def article(entity: Entity) -> None:
        if entity.wiki and not index.has_page(wiki_url(entity.wiki)):
            page = wp_page(entity.wiki)
            if page and page.paragraphs:
                index.add_page(page)
                fetched.append(page.url)

    def run() -> Tuple[List[Feature], float, float, List[Hit], List[Hit]]:
        features, focus = drop_unknown(index, *build_features(index, question, extra, entities), bool(extra))
        mass = query_mass(features, focus, cue, slot)
        need = threshold_for(mass)
        hits = index.search(features, cue, focus, limit=40, slot=slot, subjects=subjects)
        return features, mass, need, hits, _passing(hits, need, features, cue, slot)

    if use_web:
        for entity in entities[:1]:
            if entity.origin == "wikidata":
                article(entity)
    features, mass, need, hits, winners = run()
    if not winners and use_web:
        before = len(fetched)
        for entity in entities[:1]:
            article(entity)
        fetched += harvest(index, question, extra, entities, cue)
        if len(fetched) > before:
            features, mass, need, hits, winners = run()
    if winners:
        if extra:
            key = focus_key(question)
            for reply in extra:
                for term in dict.fromkeys(content(reply)):
                    index.add_relation(key, term, "learned", W_LEARNED)
        return Retrieval("answer", _render_answer(winners, mass, need, entities), "RETRIEVAL_ANSWER", winners, mass, need, fetched)
    if allow_ask and rounds < MAX_ROUNDS:
        _PENDING[session] = Pending(question, extra, rounds + 1, time.time())
        ask = _clarify(question, extra, features, hits, rivals)
        return Retrieval("ask", _render_ask(ask, hits, mass, need, use_web), "RETRIEVAL_CLARIFY", hits[:3], mass, need, fetched)
    return Retrieval("gap", _render_gap(question, extra, hits, mass, need, use_web, index), "EPISTEMIC_GAP", hits[:3], mass, need, fetched)


def _source_line(hit: Hit) -> str:
    where = hit.url or hit.locator
    title = hit.heading or hit.title
    door = f" · door {hit.door}" if hit.door and hit.door != where else ""
    return f"{title} · {where} · {hit.tier} tier{door}"


def _weight_line(hit: Hit, need: float, mass: float) -> str:
    terms = " + ".join(f"{name} {value:g}" for name, value in sorted(hit.parts.items(), key=lambda kv: -kv[1]))
    return f"{hit.score:g} against threshold {need:g} of query mass {mass:g} ; anchor {hit.anchor:g} ; {terms}"


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _render_answer(hits: Sequence[Hit], mass: float, need: float, entities: Sequence[Entity]) -> str:
    lead = hits[0]
    lines = ["modality : [CITED]", f"answer : {_trim(lead.text, 900)}"]
    if lead.paragraph and fold(lead.paragraph) != fold(lead.text):
        lines.append(f"context : {_trim(lead.paragraph, 700)}")
    lines.append(f"source : {_source_line(lead)}")
    lines.append(f"weight : {_weight_line(lead, need, mass)}")
    if entities:
        named = entities[0]
        lines.append(f"entity : {named.label}" + (f" — {named.descr}" if named.descr else ""))
    for hit in hits[1:]:
        lines.append(f"also : {_trim(hit.text, 500)}")
        lines.append(f"also source : {_source_line(hit)} · weight {hit.score:g}")
    if lead.tier == "seed":
        lines.append("note : wikipedia orients; a primary door outranks it on the same fact.")
    lines.append("route : RETRIEVAL_ANSWER")
    return "\n\n".join(lines)


def _clarify(question: str, extra: Sequence[str], features: Sequence[Feature], hits: Sequence[Hit], rivals: Sequence[Entity]) -> str:
    subject = " ".join(focus_words(strip_question(question))) or question
    if rivals:
        readings = "; ".join(f"{e.label} — {e.descr}" for e in rivals[:4])
        return f"which {subject} do you mean? candidates : {readings}. name one, or add a date, place, or field."
    base = [f for f in features if f.base and " " not in f.term]
    if hits:
        matched = {name.split(" [")[0] for name in hits[0].parts}
        missing = [f.label for f in base if f.label not in matched]
        near = "; ".join(dict.fromkeys(_trim(hit.heading or hit.title, 60) for hit in hits[:3]))
        gap = f" they miss {', '.join(missing)}." if missing else ""
        return (f"the nearest passages sit under {near}.{gap} name the person, place, period, or field "
                f"{subject} belongs to, or another name for it.")
    return f"nothing indexed matched {subject}. give another name for it, its field, or a date or place tied to it."


def _render_ask(ask: str, hits: Sequence[Hit], mass: float, need: float, web: bool) -> str:
    best = hits[0].score if hits else 0.0
    lines = ["modality : [UNKNOWN]", f"best weight : {best:g} against threshold {need:g} of query mass {mass:g}."]
    if hits:
        lines.append(f"nearest : {_trim(hits[0].text, 240)}")
    lines.append(f"searched : stacks{' and whitelisted web' if web else ''}.")
    lines.append(f"clarifying question : {ask}")
    lines.append("route : RETRIEVAL_CLARIFY")
    return "\n\n".join(lines)


def _render_gap(question: str, extra: Sequence[str], hits: Sequence[Hit], mass: float, need: float, web: bool, index: AliceIndex) -> str:
    subject = " ".join([question, *extra])
    best = hits[0].score if hits else 0.0
    lines = [
        "modality : [UNKNOWN]",
        f"DONT_KNOW : no stack sentence or whitelisted page reached threshold for {subject!r}.",
        f"best weight : {best:g} against threshold {need:g} of query mass {mass:g}.",
        f"searched : {index.sentence_count()} indexed sentences{' plus wikipedia, wikidata, and the whitelist search hands' if web else ''}.",
    ]
    for hit in hits[:3]:
        lines.append(f"nearest : {_trim(hit.text, 200)} · {_source_line(hit)} · weight {hit.score:g}")
    lines.append("route : EPISTEMIC_GAP")
    return "\n\n".join(lines)


# ---------------------------------------------------------------- cli


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m hydra_cli.alice_retrieve")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build")
    build.add_argument("--force", action="store_true")
    sub.add_parser("stats")
    ask = sub.add_parser("ask")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--offline", action="store_true")
    chat = sub.add_parser("chat")
    chat.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    index = default_index()
    if args.cmd == "build":
        started = time.perf_counter()
        rebuilt = index.ensure_built(force=args.force)
        print(f"index : {'rebuilt' if rebuilt else 'current'} in {time.perf_counter() - started:.1f}s.")
        print(f"path : {index.path}.")
        for key, value in index.stats().items():
            print(f"{key} : {value}.")
        return 0
    if args.cmd == "stats":
        index.ensure_built()
        for key, value in index.stats().items():
            print(f"{key} : {value}.")
        return 0
    if args.cmd == "ask":
        result = answer(" ".join(args.question), session="cli", web=not args.offline, allow_ask=True, index=index)
        print(result.text)
        return 0 if result.action == "answer" else 2
    while True:
        try:
            line = input("alice > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line in ("exit", "quit"):
            return 0
        if line:
            print(answer(line, session="chat", web=not args.offline, index=index).text + "\n")


if __name__ == "__main__":
    sys.exit(main())
