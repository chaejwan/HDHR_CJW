"""내려받은 페이지에서 '공고 목록'을 뽑아낸다.

사이트마다 구조가 다르므로 아래 순서로 시도한다.

1. 응답이 JSON 이면 JSON 항목 추출 (mode=json 이면 매핑 설정을 사용)
2. <script type="application/ld+json"> 의 JobPosting 구조화 데이터
3. 페이지에 박혀 있는 JSON (__NEXT_DATA__ 등) 안의 목록처럼 보이는 배열
4. <a> 링크 목록
5. 위 어느 것도 못 찾으면 본문 텍스트 해시로 '변경 여부'만 감시

어느 단계에서 뽑았는지는 결과의 method 에 남겨 UI/로그에서 확인할 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

# 목록 항목의 제목/식별자/주소/날짜로 쓰일 만한 키 이름들.
TITLE_KEYS = (
    "title", "name", "subject", "jobtitle", "recruittitle", "noticetitle",
    "postingtitle", "ttl", "headline", "label",
)
ID_KEYS = (
    "id", "seq", "no", "idx", "key", "code", "uid", "pk", "recruitno", "noticeid",
    "postingid", "jobid", "sn",
)
URL_KEYS = ("url", "link", "href", "detailurl", "pageurl", "applyurl", "permalink")
DATE_KEYS = (
    "date", "startdate", "enddate", "regdate", "createdat", "postdate",
    "opendate", "closedate", "datePosted", "period", "recruitperiod",
)

SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "#")

# 국내 채용 사이트 API 는 rtNm, rcrtTtl, sbjt 처럼 줄인 이름을 쓰는 경우가 많다.
# 정확히 아는 이름으로 못 찾으면 아래 패턴으로 한 번 더 찾는다.
FUZZY_TITLE_RE = re.compile(r"(nm|name|title|ttl|subject|subj)$", re.I)
FUZZY_ID_RE = re.compile(r"(seq|sn|no|id|idx|cd|code|key)$", re.I)
TITLE_MIN_LEN = 2
TITLE_MAX_LEN = 200


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _pick(obj: dict, keys):
    """obj 에서 keys 중 먼저 발견되는 (실제키, 값) 을 돌려준다."""
    normalized = {_norm_key(k): k for k in obj}
    for wanted in keys:
        real = normalized.get(_norm_key(wanted))
        if real is not None and obj[real] not in (None, "", [], {}):
            return real, obj[real]
    return None, None


def _fuzzy_pick(obj: dict, pattern, want_text: bool):
    """이름 규칙(rtNm, rcrtTtl …)으로 제목/식별자처럼 보이는 값을 찾는다."""
    best = None
    for key, value in obj.items():
        if not pattern.search(_norm_key(key)):
            continue
        if want_text:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not (TITLE_MIN_LEN <= len(text) <= TITLE_MAX_LEN):
                continue
            if best is None or len(text) > len(best[1]):
                best = (key, text)
        else:
            if isinstance(value, (str, int)) and str(value).strip():
                return key, value
    return best if best else (None, None)


def pick_title(obj: dict):
    key, value = _pick(obj, TITLE_KEYS)
    if key is not None:
        return key, value
    return _fuzzy_pick(obj, FUZZY_TITLE_RE, want_text=True)


def pick_id(obj: dict):
    key, value = _pick(obj, ID_KEYS)
    if key is not None:
        return key, value
    return _fuzzy_pick(obj, FUZZY_ID_RE, want_text=False)


def _as_text(value) -> str:
    if isinstance(value, (str, int, float)):
        return re.sub(r"\s+", " ", str(value)).strip()
    if isinstance(value, dict):
        for key in ("text", "value", "name", "title"):
            if key in value:
                return _as_text(value[key])
    if isinstance(value, list) and value:
        return _as_text(value[0])
    return ""


def digest(*parts: str) -> str:
    joined = "".join(part or "" for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def canonical_url(url: str) -> str:
    """비교용 주소 정규화 (조각/추적 파라미터 제거)."""
    if not url:
        return ""
    parts = urlparse(url)
    query = "&".join(
        piece for piece in parts.query.split("&")
        if piece and not piece.lower().startswith(("utm_", "fbclid=", "gclid=", "_ga="))
    )
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, query, ""))


# ---------------------------------------------------------------- HTML 파싱

@dataclass
class ParsedDoc:
    title: str = ""
    anchors: list = field(default_factory=list)   # [{"href","title"}]
    scripts: list = field(default_factory=list)   # [{"type","id","data"}]
    text: str = ""


class _DocParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doc = ParsedDoc()
        self._skip = []
        self._anchor_stack = []
        self._script_attrs = None
        self._script_buf = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            self._script_attrs = attrs_map
            self._script_buf = []
        if tag in self._SKIP_TAGS:
            self._skip.append(tag)
            return
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            self._anchor_stack.append({"href": attrs_map.get("href", ""), "parts": []})

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._push_text(" ")

    def handle_endtag(self, tag):
        if tag == "script" and self._script_attrs is not None:
            self.doc.scripts.append({
                "type": self._script_attrs.get("type", ""),
                "id": self._script_attrs.get("id", ""),
                "data": "".join(self._script_buf),
            })
            self._script_attrs = None
            self._script_buf = []
        if tag in self._SKIP_TAGS:
            if self._skip and self._skip[-1] == tag:
                self._skip.pop()
            return
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor_stack:
            anchor = self._anchor_stack.pop()
            text = re.sub(r"\s+", " ", "".join(anchor["parts"])).strip()
            self.doc.anchors.append({"href": anchor["href"], "title": text})

    def _push_text(self, data: str):
        for anchor in self._anchor_stack:
            anchor["parts"].append(data)

    def handle_data(self, data):
        if self._script_attrs is not None:
            self._script_buf.append(data)
        if self._skip:
            return
        if self._in_title:
            self.doc.title += data
            return
        self.doc.text += data
        self._push_text(data)


def parse_html(html: str) -> ParsedDoc:
    parser = _DocParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass  # 깨진 마크업이어도 그때까지 모은 내용은 쓴다
    doc = parser.doc
    doc.title = re.sub(r"\s+", " ", doc.title).strip()
    doc.text = re.sub(r"\s+", " ", doc.text).strip()
    return doc


# ---------------------------------------------------------------- JSON 탐색

def dig(data, path: str):
    """'data.list.0.items' 같은 점 표기 경로로 값을 꺼낸다."""
    current = data
    for token in [t for t in re.split(r"[.\[\]]+", path or "") if t]:
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return current


def _looks_like_item(obj) -> bool:
    if not isinstance(obj, dict):
        return False
    key, _ = pick_title(obj)
    return key is not None


def find_item_arrays(data, depth: int = 0, path: str = ""):
    """제목처럼 보이는 키를 가진 딕셔너리 배열을 모두 찾는다."""
    found = []
    if depth > 8:
        return found
    if isinstance(data, list):
        items = [row for row in data if _looks_like_item(row)]
        if len(items) >= 2 and len(items) >= len(data) / 2:
            found.append((path, items))
        for idx, row in enumerate(data[:50]):
            found.extend(find_item_arrays(row, depth + 1, f"{path}[{idx}]"))
    elif isinstance(data, dict):
        for key, value in data.items():
            found.extend(find_item_arrays(value, depth + 1, f"{path}.{key}" if path else str(key)))
    return found


def item_from_object(obj: dict, base_url: str, mapping: dict | None = None):
    """딕셔너리 하나를 공고 항목으로 바꾼다. 제목이 없으면 None."""
    mapping = mapping or {}

    def mapped(field_name, fallback_keys, picker=None):
        configured = mapping.get(field_name)
        if configured:
            value = dig(obj, configured)
            return _as_text(value) if value not in (None, "") else ""
        if picker is not None:
            _, value = picker(obj)
        else:
            _, value = _pick(obj, fallback_keys)
        return _as_text(value)

    title = mapped("title_field", TITLE_KEYS, pick_title)
    if not title:
        return None
    raw_id = mapped("id_field", ID_KEYS, pick_id)
    url = mapped("url_field", URL_KEYS)
    template = mapping.get("url_template") or ""
    if template:
        try:
            url = re.sub(r"\{(\w+)\}", lambda m: _as_text(dig(obj, m.group(1))), template)
        except Exception:
            pass
    url = urljoin(base_url, url) if url else base_url
    date = mapped("date_field", DATE_KEYS)
    if raw_id:
        item_id = digest(str(raw_id), title)
    else:
        item_id = digest(canonical_url(url), title)
    return {
        "id": item_id,
        "title": title,
        "url": url,
        "date": date,
        "raw_id": raw_id,
    }


def items_from_jsonld(doc: ParsedDoc, base_url: str) -> list:
    items = []
    for script in doc.scripts:
        if "ld+json" not in (script.get("type") or ""):
            continue
        try:
            data = json.loads(script["data"])
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "jobposting" in str(node.get("@type", "")).lower():
                    item = item_from_object(node, base_url)
                    if item:
                        items.append(item)
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return items


def items_from_embedded_json(doc: ParsedDoc, base_url: str) -> list:
    """__NEXT_DATA__ 등 페이지에 박힌 JSON 에서 가장 그럴듯한 목록을 고른다."""
    best = []
    for script in doc.scripts:
        payload = (script.get("data") or "").strip()
        script_type = (script.get("type") or "").lower()
        if not payload:
            continue
        if "json" not in script_type and script.get("id") not in ("__NEXT_DATA__", "__NUXT_DATA__"):
            continue
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        for _path, rows in find_item_arrays(data):
            converted = [it for it in (item_from_object(r, base_url) for r in rows) if it]
            if len(converted) > len(best):
                best = converted
    return best


def items_from_anchors(doc: ParsedDoc, base_url: str) -> list:
    items = []
    seen = set()
    for anchor in doc.anchors:
        href = (anchor.get("href") or "").strip()
        title = (anchor.get("title") or "").strip()
        if not href or not title:
            continue
        if href.lower().startswith(SKIP_SCHEMES):
            continue
        url = canonical_url(urljoin(base_url, href))
        if not url.startswith("http"):
            continue
        key = url + "|" + title
        if key in seen:
            continue
        seen.add(key)
        items.append({"id": digest(url, title), "title": title[:160], "url": url, "date": "", "raw_id": ""})
    return items


# ---------------------------------------------------------------- 진입점

@dataclass
class ExtractResult:
    items: list = field(default_factory=list)
    method: str = "none"
    fingerprint: str = ""
    note: str = ""


def _apply_filters(items: list, site: dict) -> list:
    def compile_pattern(key):
        pattern = (site.get(key) or "").strip()
        if not pattern:
            return None
        try:
            return re.compile(pattern, re.I)
        except re.error:
            return None

    url_re = compile_pattern("url_pattern")
    title_re = compile_pattern("title_pattern")
    exclude_re = compile_pattern("exclude_pattern")

    kept = []
    for item in items:
        url, title = item.get("url") or "", item.get("title") or ""
        if url_re and not url_re.search(url):
            continue
        if title_re and not title_re.search(title):
            continue
        if exclude_re and (exclude_re.search(url) or exclude_re.search(title)):
            continue
        kept.append(item)
    return kept


def items_from_dom(dom_items: list, base_url: str, template: str = "") -> list:
    """브라우저에서 선택자로 읽은 요소들을 공고 항목으로 바꾼다."""
    items = []
    for row in dom_items:
        title = re.sub(r"\s+", " ", (row.get("title") or "")).strip()
        if not title:
            continue
        raw_id = str(row.get("id") or "").strip()
        url = (row.get("url") or "").strip()
        if template and raw_id:
            url = template.replace("{id}", raw_id)
        url = urljoin(base_url, url) if url else base_url
        item_id = digest(raw_id, title) if raw_id else digest(canonical_url(url), title)
        items.append({"id": item_id, "title": title, "url": url, "date": "", "raw_id": raw_id})
    return items


def extract(site: dict, fetched) -> ExtractResult:
    """사이트 설정과 응답을 받아 공고 목록을 만든다."""
    mode = site.get("mode") or "auto"
    # 목록만 돌려주는 주소(API·조각)에서는 링크가 상대 주소로 오므로,
    # 사람이 볼 주소가 지정돼 있으면 그것을 기준으로 푼다.
    link_base = (site.get("home_url") or "").strip() or fetched.url or site.get("url") or ""
    dom_items = getattr(fetched, "dom_items", None)
    if dom_items:
        found = items_from_dom(dom_items, fetched.url or site.get("url") or "",
                               (site.get("json") or {}).get("url_template", ""))
        if found:
            result = ExtractResult(items=found, method="selector",
                                   fingerprint=digest(fetched.text[:200000]))
            return _finish(result, site)
    base_url = fetched.url or site.get("url") or ""
    mapping = site.get("json") or {}
    fingerprint = ""
    items = []
    method = "none"
    note = ""

    if mode == "json" or fetched.looks_json:
        try:
            data = json.loads(fetched.text)
        except (ValueError, TypeError) as exc:
            if mode == "json":
                return ExtractResult([], "none", digest(fetched.text), f"JSON 파싱 실패: {exc}")
            data = None
        if data is not None:
            rows = dig(data, mapping.get("items_path", "")) if mapping.get("items_path") else None
            if not isinstance(rows, list):
                rows = max((r for _p, r in find_item_arrays(data)), key=len, default=[])
            converted = [it for it in (item_from_object(r, base_url, mapping) for r in rows) if it]
            if converted:
                items, method = converted, "json"
            fingerprint = digest(json.dumps(data, sort_keys=True, ensure_ascii=False)[:200000])

    if not items:
        doc = parse_html(fetched.text)
        fingerprint = fingerprint or digest(doc.text[:200000])

        # 후보를 순서대로 만들되, 필터를 통과한 항목이 남는 첫 번째 방식을 쓴다.
        # (메뉴 목록이 먼저 잡혀 진짜 공고 링크를 가리는 일을 막는다)
        candidates = []
        if mode in ("auto", "browser"):
            candidates.append(("jsonld", lambda: items_from_jsonld(doc, base_url)))
            candidates.append(("embedded-json", lambda: items_from_embedded_json(doc, base_url)))
        if mode != "json":
            candidates.append(("links", lambda: items_from_anchors(doc, link_base)))

        fallback = None
        for name, build in candidates:
            found = build()
            if not found:
                continue
            if _apply_filters(found, site):
                items, method = found, name
                break
            if fallback is None:
                fallback = (name, found)   # 필터에 다 걸렸지만 뭔가는 읽은 방식
        if not items and fallback:
            method, items = fallback[0], fallback[1]
        if not items:
            method = "fingerprint"
            note = "목록을 찾지 못해 페이지 내용 변경만 감시합니다."

    return _finish(ExtractResult(items=items, method=method, fingerprint=fingerprint, note=note), site)


def _stabilize_ids(items: list) -> None:
    """주소가 항목마다 다르면 주소만으로 식별한다.

    링크에서 읽은 제목에는 남은 일수(D-114)나 조회수처럼 매일 바뀌는 값이
    섞여 들어오는 일이 잦다. 그대로 두면 같은 공고가 매일 새 공고로 보인다.
    """
    urls = [canonical_url(item.get("url") or "") for item in items]
    distinct = {url for url in urls if url and urlparse(url).path not in ("", "/")}
    if len(distinct) != len([u for u in urls if u]):
        return                      # 주소가 겹치는 항목이 있으면 제목도 함께 본다
    for item, url in zip(items, urls):
        if not item.get("raw_id") and url and urlparse(url).path not in ("", "/"):
            item["id"] = digest(url)


def _finish(result: ExtractResult, site: dict) -> ExtractResult:
    """식별자 안정화 · 필터 적용 · 중복 제거 · 최대 개수 제한."""
    _stabilize_ids(result.items)
    items, note = result.items, result.note
    if items:
        before = len(items)
        items = _apply_filters(items, site)
        deduped, seen = [], set()
        for item in items:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            deduped.append(item)
        items = deduped
        max_items = int(site.get("max_items") or 300)
        if len(items) > max_items:
            note = f"항목이 {len(items)}개라 앞의 {max_items}개만 사용합니다. 필터를 지정해 보세요."
            items = items[:max_items]
        elif before != len(items):
            note = f"{before}개 중 필터를 통과한 {len(items)}개를 사용합니다."
    result.items, result.note = items, note
    return result
