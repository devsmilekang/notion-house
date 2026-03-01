"""
임장 기록 노션 업로드 스크립트
===============================
template.md 파일에 작성된 임장 데이터를 파싱하여 노션 데이터베이스에 자동 업로드합니다.

사용법:
  1. template.md에 임장 데이터를 작성합니다.
  2. python upload_to_notion.py 를 실행합니다.
  3. 성공 시 생성된 노션 페이지 URL이 출력됩니다.

필요 환경:
  - .env 파일에 NOTION_API_KEY 설정
  - 노션 Integration이 해당 데이터베이스에 연결되어 있어야 함
"""

import urllib.request
import json
import ssl
import os
import sys
import re


# ─────────────────────────── 설정 ───────────────────────────

def load_env(filepath=".env"):
    """간단한 .env 파일 로더 (dotenv 라이브러리 없이 동작)"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


load_env()

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
NOTION_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"

if not NOTION_API_KEY or not DATABASE_ID:
    print("❌ NOTION_API_KEY 또는 NOTION_DATABASE_ID가 설정되지 않았습니다. .env 파일을 확인해주세요.")
    sys.exit(1)


# ─────────────────────────── 템플릿 파서 ───────────────────────────

def parse_template(filepath: str) -> dict:
    """
    template.md 파일을 파싱하여 임장 데이터 딕셔너리를 반환합니다.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    full_path = os.path.join(script_dir, filepath)

    with open(full_path, "r", encoding="utf-8") as f:
        content = f.read()

    data = {}

    # ── 0. 기본 정보 ──
    data["아파트명"] = _extract_field(content, "아파트명(필수)")
    data["방문일"] = _extract_field(content, "방문일")
    data["한줄요약"] = _extract_field(content, "한줄 요약")

    # 평형별 시세 테이블 파싱
    data["평형별시세"] = _extract_price_table(content)

    # ── 1. 사전 조사 ──
    data["교통노드"] = _extract_field(content, "교통 노드")
    data["단지스펙"] = _extract_field(content, "단지 스펙")
    data["인프라"] = _extract_field(content, "인프라")
    data["학군정보"] = _extract_field(content, "학군 정보")

    # ── 2. 현장 실사 — 점수 ──
    data["현장교통"] = _extract_number(content, "현장교통")
    data["현장학군"] = _extract_number(content, "현장학군")
    data["현장상권"] = _extract_number(content, "현장상권")
    data["현장환경"] = _extract_number(content, "현장환경")
    data["현장단지"] = _extract_number(content, "현장단지")
    data["발품요약"] = _extract_field(content, "발품 요약")

    # ── 2.1 오감 체감 ──
    data["오감체감"] = _extract_todo_items(content)

    # ── 2.2 일조량 ──
    data["관찰시각"] = _extract_field(content, "관찰 시각")
    data["일조량"] = _extract_table_rows(content)

    # ── 3. 사진 ──
    data["사진_urls"] = _extract_photo_urls(content)

    # ── 추가 의견 ──
    data["추가의견"] = _extract_additional_comments(content)

    return data


def _extract_field(content: str, field_name: str) -> str:
    """
    '- **필드명:** 값' 패턴에서 값을 추출합니다.
    같은 줄 내에서만 값을 추출하며, 빈 필드는 빈 문자열을 반환합니다.
    """
    escaped = re.escape(field_name)
    # 줄 단위로 검색하여 해당 필드의 값만 정확히 추출
    for line in content.split('\n'):
        line = line.strip()
        match = re.match(rf'-\s*\*\*{escaped}:\*\*\s*(.*)', line)
        if match:
            return match.group(1).strip()
    return ""


def _extract_number(content: str, field_name: str) -> int | None:
    """
    '- **필드명:** 숫자' 패턴에서 숫자를 추출합니다.
    """
    value = _extract_field(content, field_name)
    if value:
        # 숫자만 추출
        nums = re.findall(r'\d+', value)
        if nums:
            n = int(nums[0])
            return max(1, min(5, n))  # 1~5 범위 제한
    return None


def _extract_todo_items(content: str) -> list:
    """
    '- [x] **라벨:** 텍스트' 또는 '- [ ] **라벨:** 텍스트' 패턴을 파싱합니다.
    """
    items = []
    pattern = r'-\s*\[([ xX])\]\s*\*\*(.+?):\*\*\s*(.*?)(?:\r?\n|$)'
    for match in re.finditer(pattern, content):
        checked = match.group(1).strip().lower() == 'x'
        label = match.group(2).strip() + ":"
        text = match.group(3).strip()
        items.append({"label": label, "text": text, "checked": checked})
    return items


def _extract_generic_table(content: str, header_keyword: str, col_count: int) -> list:
    """
    마크다운 테이블에서 데이터 행을 추출합니다 (헤더와 구분선 제외).
    header_keyword: 헤더 행에서 찾을 키워드 (예: '동 번호', '평형')
    col_count: 열 개수
    """
    rows = []
    # 동적 열 수에 맞는 패턴 생성
    cell_pattern = r'\|\s*(.+?)\s*' * col_count + r'\|'
    in_table = False
    skip_count = 0

    for line in content.split('\n'):
        line = line.strip()
        if re.match(rf'\|\s*{re.escape(header_keyword)}', line):
            in_table = True
            skip_count = 2  # 헤더 + 구분선
            continue
        if in_table:
            skip_count -= 1
            if skip_count > 0:
                continue
            match = re.match(cell_pattern, line)
            if match:
                cells = [match.group(i).strip() for i in range(1, col_count + 1)]
                # 빈 행은 건너뛰기
                if any(cell for cell in cells):
                    rows.append(cells)
            else:
                break  # 테이블 끝
    return rows


def _extract_table_rows(content: str) -> list:
    """일조량 테이블 (동 번호 | 확인 층수 | 일조 상태 | 메모) 추출"""
    return _extract_generic_table(content, "동 번호", 4)


def _extract_price_table(content: str) -> list:
    """
    평형별 시세 테이블을 추출합니다.
    반환: [{"평형": str, "매매": str, "전세": str, "비고": str}, ...]
    """
    raw_rows = _extract_generic_table(content, "평형", 4)
    result = []
    for row in raw_rows:
        result.append({
            "평형": row[0],
            "매매": row[1],
            "전세": row[2] if len(row) > 2 else "",
            "비고": row[3] if len(row) > 3 else "",
        })
    return result


def _extract_photo_urls(content: str) -> list:
    """
    ■ 3. 임장 사진 아카이브 섹션에서 URL을 추출합니다.
    """
    urls = []
    # 사진 섹션 찾기
    section_pattern = r'##\s*■\s*3\.\s*임장 사진.*?\n(.*?)(?=\n---|\n##\s*■|$)'
    match = re.search(section_pattern, content, re.DOTALL)
    if match:
        section = match.group(1)
        # http로 시작하는 URL 추출
        url_pattern = r'(https?://\S+)'
        urls = re.findall(url_pattern, section)
    return urls


def _extract_additional_comments(content: str) -> str:
    """
    ■ 추가 의견 섹션의 내용을 추출합니다.
    '>' 로 시작하는 가이드 라인은 제외합니다.
    """
    # 추가 의견 섹션 찾기
    section_pattern = r'##\s*■\s*추가 의견.*?\n(.*?)$'
    match = re.search(section_pattern, content, re.DOTALL)
    if match:
        lines = match.group(1).split('\n')
        # '>' 로 시작하는 blockquote 가이드 라인 제거
        filtered = [line for line in lines if not line.strip().startswith('>')]
        text = '\n'.join(filtered).strip()
        return text
    return ""


# ─────────────────────────── 헬퍼 함수 ───────────────────────────

def rich_text(content: str, bold: bool = False, italic: bool = False, code: bool = False) -> dict:
    """Rich text 객체 하나를 생성합니다."""
    return {
        "type": "text",
        "text": {"content": content, "link": None},
        "annotations": {
            "bold": bold,
            "italic": italic,
            "strikethrough": False,
            "underline": False,
            "code": code,
            "color": "default",
        },
    }


def heading_block(level: int, text: str) -> dict:
    """heading_1, heading_2, heading_3 블록을 생성합니다."""
    key = f"heading_{level}"
    return {
        "object": "block",
        "type": key,
        key: {
            "rich_text": [rich_text(text)],
            "color": "default",
            "is_toggleable": False,
        },
    }


def paragraph_block(text: str, bold: bool = False) -> dict:
    """paragraph 블록을 생성합니다."""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [rich_text(text, bold=bold)] if text else [],
            "color": "default",
        },
    }


def bulleted_list_block(text_segments: list) -> dict:
    """
    bulleted_list_item 블록을 생성합니다.
    text_segments: [{"content": str, "bold": bool}, ...] 또는 str 리스트
    """
    rt = []
    for seg in text_segments:
        if isinstance(seg, str):
            rt.append(rich_text(seg))
        else:
            rt.append(rich_text(seg["content"], bold=seg.get("bold", False), code=seg.get("code", False)))
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": rt,
            "color": "default",
        },
    }


def to_do_block(text: str, checked: bool = False) -> dict:
    """to_do 체크박스 블록을 생성합니다."""
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": [rich_text(text)],
            "checked": checked,
            "color": "default",
        },
    }


def divider_block() -> dict:
    """divider 블록을 생성합니다."""
    return {
        "object": "block",
        "type": "divider",
        "divider": {},
    }


def callout_block(text_segments: list, emoji: str = "💡", color: str = "gray_background") -> dict:
    """callout 블록을 생성합니다."""
    rt = []
    for seg in text_segments:
        if isinstance(seg, str):
            rt.append(rich_text(seg))
        else:
            rt.append(rich_text(seg["content"], bold=seg.get("bold", False), code=seg.get("code", False)))
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": rt,
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        },
    }


def table_block(headers: list, rows: list) -> dict:
    """table 블록을 생성합니다."""
    table_width = len(headers)

    def make_row(cells: list) -> dict:
        return {
            "type": "table_row",
            "table_row": {
                "cells": [[rich_text(str(cell))] for cell in cells]
            },
        }

    children = [make_row(headers)]
    for row in rows:
        padded = list(row) + [""] * (table_width - len(row))
        children.append(make_row(padded[:table_width]))

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": table_width,
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    }


# ─────────────────────────── 페이지 빌더 ───────────────────────────

def build_properties(data: dict) -> dict:
    """노션 데이터베이스 속성(Properties)을 구성합니다."""
    props = {
        "아파트명": {
            "title": [rich_text(data["아파트명"])]
        },
    }

    # 평형별 시세 → 호가(매/전) 합치기
    price_rows = data.get("평형별시세", [])
    if price_rows:
        # 첫 번째 평형을 대표 평형으로 설정 (select)
        first_pyung = price_rows[0].get("평형", "")
        if first_pyung:
            props["평형"] = {"select": {"name": first_pyung}}

        # 모든 평형 호가를 합쳐서 호가(매/전)에 저장
        hoga_parts = []
        for row in price_rows:
            p = row.get("평형", "")
            m = row.get("매매", "")
            j = row.get("전세", "")
            part = f"{p}"
            if m:
                part += f" 매매 {m}"
            if j:
                part += f" / 전세 {j}"
            hoga_parts.append(part)
        if hoga_parts:
            props["호가(매/전)"] = {"rich_text": [rich_text(" | ".join(hoga_parts))]}

    if data.get("방문일"):
        props["방문일"] = {"date": {"start": data["방문일"]}}

    score_fields = {
        "현장교통": "현장교통(5점)",
        "현장학군": "현장학군(5점)",
        "현장상권": "현장상권(5점)",
        "현장환경": "현장환경(5점)",
        "현장단지": "현장단지(5점)",
    }
    for data_key, db_key in score_fields.items():
        if data.get(data_key) is not None:
            props[db_key] = {"number": int(data[data_key])}

    if data.get("발품요약"):
        props["발품 요약"] = {"rich_text": [rich_text(data["발품요약"])]}

    return props


def build_page_content(data: dict) -> list:
    """template.md 구조에 맞게 본문 블록(children)을 구성합니다."""
    blocks = []

    # ── 제목 ──
    blocks.append(heading_block(1, f"🏢 [{data['아파트명']}] 종합 입지 분석 보고서"))

    # ── Callout: 종합 점수 + 한줄 요약 ──
    callout_parts = []
    callout_parts.append({"content": "종합 점수: ", "bold": True})
    callout_parts.append({"content": "💯 수식 필드 (하단 점수 입력 시 자동 계산)", "code": True})
    callout_parts.append({"content": "\n한줄 요약: ", "bold": True})
    callout_parts.append(data.get("한줄요약") or "미입력")
    blocks.append(callout_block(callout_parts, emoji="📊", color="blue_background"))

    # ── 평형별 시세 테이블 ──
    price_rows = data.get("평형별시세", [])
    if price_rows:
        blocks.append(heading_block(3, "💰 평형별 시세 정보"))
        price_headers = ["평형", "호가(매매)", "호가(전세)", "비고"]
        price_data = [[r.get("평형", ""), r.get("매매", ""), r.get("전세", ""), r.get("비고", "")] for r in price_rows]
        blocks.append(table_block(price_headers, price_data))

    blocks.append(divider_block())

    # ── 1. 사전 조사 ──
    blocks.append(heading_block(2, "1. 사전 조사 (Static Data)"))

    static_items = [
        ("교통 노드: ", data.get("교통노드", "")),
        ("단지 스펙: ", data.get("단지스펙", "")),
        ("인프라: ", data.get("인프라", "")),
        ("학군 정보: ", data.get("학군정보", "")),
    ]
    for label, value in static_items:
        if value:
            blocks.append(bulleted_list_block([
                {"content": label, "bold": True},
                value,
            ]))

    blocks.append(divider_block())

    # ── 2. 현장 실사 ──
    blocks.append(heading_block(2, "2. 현장 실사 (Dynamic Data)"))

    # 2.1 오감 및 분위기 체감
    blocks.append(heading_block(3, "2.1 오감 및 분위기 체감"))

    ogam_items = data.get("오감체감", [])
    for item in ogam_items:
        label = item.get("label", "")
        text = item.get("text", "")
        checked = item.get("checked", False)
        display = f" {label} {text}" if text else f" {label}"
        blocks.append(to_do_block(display, checked=checked))

    # 2.2 일조량
    observation_time = data.get("관찰시각", "")
    blocks.append(heading_block(3, f"2.2 동/층별 실시간 일조량 (관찰 시각: {observation_time})"))

    sunlight_headers = ["동 번호", "확인 층수", "일조 상태", "메모 (그림자 위치 및 조망)"]
    sunlight_rows = data.get("일조량", [])
    if sunlight_rows:
        blocks.append(table_block(sunlight_headers, sunlight_rows))
    else:
        blocks.append(paragraph_block("(일조량 데이터 없음)"))

    blocks.append(divider_block())

    # ── 3. 사진 ──
    blocks.append(heading_block(2, "3. 임장 사진 아카이브 (Visual Proof)"))

    photo_urls = data.get("사진_urls", [])
    if photo_urls:
        for url in photo_urls:
            blocks.append({
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": url},
                },
            })
    else:
        blocks.append(callout_block(
            ["📸 사진은 노션 페이지에서 직접 첨부해주세요. (노션 API를 통한 로컬 파일 첨부는 제한됩니다)"],
            emoji="📸",
            color="yellow_background",
        ))

    # ── 추가 의견 ──
    blocks.append(heading_block(2, "추가 의견"))

    additional = data.get("추가의견", "")
    if additional:
        for line in additional.strip().split("\n"):
            line = line.strip()
            if line:
                blocks.append(paragraph_block(line))
    else:
        blocks.append(paragraph_block("(추가 의견 없음)"))

    return blocks


# ─────────────────────────── API 호출 ───────────────────────────

def notion_api_request(endpoint: str, payload: dict) -> dict:
    """노션 API에 POST 요청을 보냅니다."""
    url = f"{NOTION_API_BASE}{endpoint}"
    data = json.dumps(payload).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    context = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(req, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"❌ Notion API 에러 ({e.code}):")
        try:
            err = json.loads(error_body)
            print(json.dumps(err, indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(error_body)
        sys.exit(1)


def create_imjang_page(data: dict) -> str:
    """임장 데이터를 받아서 노션 페이지를 생성하고, 생성된 페이지 URL을 반환합니다."""
    properties = build_properties(data)
    children = build_page_content(data)

    payload = {
        "parent": {"database_id": DATABASE_ID},
        "icon": {"type": "emoji", "emoji": "🏢"},
        "properties": properties,
        "children": children,
    }

    result = notion_api_request("/pages", payload)
    page_url = result.get("url", "")
    return page_url


# ─────────────────────────── 메인 실행 ───────────────────────────

if __name__ == "__main__":
    template_path = "template.md"
    auto_confirm = False

    # 커맨드라인 인자 파싱
    args = sys.argv[1:]
    for arg in args:
        if arg in ("--yes", "-y"):
            auto_confirm = True
        else:
            template_path = arg

    print(f"📄 템플릿 파일 읽는 중: {template_path}")
    data = parse_template(template_path)

    # 필수 필드 체크
    if not data.get("아파트명"):
        print("❌ 아파트명이 입력되지 않았습니다. template.md를 확인해주세요.")
        sys.exit(1)

    # 파싱 결과 미리보기
    print(f"\n{'='*50}")
    print(f"📋 파싱 결과 미리보기")
    print(f"{'='*50}")
    print(f"  🏢 아파트명: {data['아파트명']}")
    print(f"  📐 평형: {data.get('평형', '-')}")
    print(f"  💰 호가: {data.get('호가', '-')}")
    print(f"  📅 방문일: {data.get('방문일', '-')}")
    print(f"  📝 한줄요약: {data.get('한줄요약', '-')}")

    scores = []
    for key in ["현장교통", "현장학군", "현장상권", "현장환경", "현장단지"]:
        val = data.get(key)
        scores.append(f"{key}={val}" if val else f"{key}=-")
    print(f"  ⭐ 점수: {' / '.join(scores)}")

    ogam = data.get("오감체감", [])
    print(f"  ✅ 오감 체감 항목: {len(ogam)}개")

    sunlight = data.get("일조량", [])
    print(f"  ☀️ 일조량 데이터: {len(sunlight)}행")

    photos = data.get("사진_urls", [])
    print(f"  📷 사진 URL: {len(photos)}개")

    additional = data.get("추가의견", "")
    print(f"  💬 추가 의견: {'있음' if additional else '없음'}")
    print(f"{'='*50}\n")

    # 업로드 확인
    if not auto_confirm:
        confirm = input("🚀 위 내용으로 노션에 업로드하시겠습니까? (y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ 업로드가 취소되었습니다.")
            sys.exit(0)

    print("\n🚀 노션에 임장 기록을 업로드 중...")
    page_url = create_imjang_page(data)
    print(f"\n✅ 성공적으로 노션에 임장 기록을 업로드했습니다! 🎉")
    print(f"📎 페이지 URL: {page_url}")
