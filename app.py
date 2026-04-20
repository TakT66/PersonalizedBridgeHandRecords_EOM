#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bridge Board Image Extractor — Streamlit Web Edition
Μεταφορά της desktop εφαρμογής σε web app.
"""

import io
import re
import sys
import math
import pandas as pd
import tempfile
import threading
from datetime import date, timedelta, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FONT_PATH = BASE_DIR / "DejaVuSans.ttf"
FONT_BOLD_PATH = BASE_DIR / "DejaVuSans-Bold.ttf"
FONT_BOLD_ITALIC_PATH = BASE_DIR / "DejaVuSans-BoldOblique.ttf"

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

# ── endplay import (DDS) ────────────────────────────────────────────────────
try:
    from endplay.types import Deal, Denom, Player
    from endplay.dds import calc_dd_table
    DDS_AVAILABLE = True
except Exception:
    DDS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants (identical to desktop version)
# ---------------------------------------------------------------------------
COLS            = 3
ROWS            = 5
BOARDS_PER_PAGE = COLS * ROWS
MARGIN          = 40
PADDING         = 16
A4_W, A4_H      = 1240, 1754

SUIT_ORDER  = ["S", "H", "D", "C"]
SUIT_SYMBOL = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
SUIT_COLOR  = {
    "S": (0,   0,   0),
    "H": (180, 30,  30),
    "D": (160, 80,  0),
    "C": (0,   110, 0),
}
HCP_VALUE = {"A": 4, "K": 3, "Q": 2, "J": 1}
DENOM_MAP = {"NT": "nt", "S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}
PLAYER_MAP = {"N": "north", "S": "south", "E": "east", "W": "west"}

# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
VALID_FILE = Path(__file__).resolve().parent / "Valid_Registration_Numbers.txt"

def load_valid_numbers():
    if not VALID_FILE.exists():
        return set()
    with open(VALID_FILE, encoding="utf-8", errors="ignore") as f:
        return {line.strip() for line in f if line.strip()}

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def make_font(size):
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except Exception:
        return ImageFont.load_default()

def make_bold_font(size):
    try:
        return ImageFont.truetype(str(FONT_BOLD_PATH), size)
    except Exception:
        return make_font(size)


def make_bold_italic_font(size):
    try:
        return ImageFont.truetype(str(FONT_BOLD_ITALIC_PATH), size)
    except Exception:
        return make_bold_font(size)

_MI  = Image.new("RGB", (4, 4))
_MD  = ImageDraw.Draw(_MI)

def tw(text, font):
    try:
        b = _MD.textbbox((0, 0), text, font=font)
        return b[2] - b[0]
    except Exception:
        return max(1, len(text)) * max(6, font.size // 2)

def th(font):
    try:
        b = _MD.textbbox((0, 0), "Ag", font=font)
        return b[3] - b[1]
    except Exception:
        return font.size + 2

# ---------------------------------------------------------------------------
# Hand / HCP / KRHCP helpers (unchanged from desktop)
# ---------------------------------------------------------------------------
def parse_hand(hand_str):
    parts = hand_str.split(".")
    if len(parts) != 4:
        return {s: "" for s in SUIT_ORDER}
    return {suit: ranks for suit, ranks in zip(SUIT_ORDER, parts)}

def calc_hcp(hand_str):
    return sum(HCP_VALUE.get(ch.upper(), 0) for ch in hand_str)

def calc_krhcp(hand_str):
    hand = parse_hand(hand_str)
    RANKS = "AKQJT98765432"
    def rank_idx(r):
        return RANKS.index(r) if r in RANKS else 12
    def suit_krhcp(ranks_str):
        if not ranks_str or ranks_str == "-":
            length, cards = 0, []
        else:
            length = len(ranks_str)
            cards  = list(ranks_str.upper())
        has      = lambda r: r in cards
        n_higher = lambda r: sum(1 for c in cards if rank_idx(c) < rank_idx(r))
        pts = 0.0
        if has("A"):  pts += 4
        if has("K"):  pts += 3
        if has("Q"):  pts += 2
        if has("J"):  pts += 1
        if has("T"):  pts += 0.5
        if 2 <= length <= 6 and has("T"):
            if has("J") or n_higher("T") >= 2:
                pts += 0.5
        if 2 <= length <= 6 and has("9"):
            if has("8") or has("T") or n_higher("9") == 2:
                pts += 0.5
        if 4 <= length <= 6 and has("9") and not has("8") and not has("T"):
            if n_higher("9") == 3:
                pts += 0.5
        if length >= 7 and (not has("Q") or not has("J")):
            pts += 1
        if length >= 8 and not has("Q"):
            pts += 1
        if length >= 9 and not has("Q") and not has("J"):
            pts += 1
        pts = pts * length / 10
        if has("A"):  pts += 3
        if has("K") and length >= 2: pts += 2
        if has("K") and length == 1: pts += 0.5
        if has("Q") and length >= 3 and (has("A") or has("K")): pts += 1
        if has("Q") and length >= 3 and not has("A") and not has("K"): pts += 0.75
        if has("Q") and length == 2 and (has("A") or has("K")): pts += 0.5
        if has("Q") and length == 2 and not has("A") and not has("K"): pts += 0.25
        if has("J") and n_higher("J") == 2: pts += 0.5
        if has("J") and n_higher("J") == 1: pts += 0.25
        if has("T") and n_higher("T") == 2: pts += 0.25
        if has("T") and has("9") and n_higher("T") == 1: pts += 0.25
        if length == 0: pts += 3
        if length == 1: pts += 2
        if length == 2: pts += 1
        return pts
    suit_totals = [suit_krhcp(hand[s]) for s in SUIT_ORDER]
    total = sum(suit_totals) - 1
    lengths = sorted([len(hand[s]) if hand[s] and hand[s] != "-" else 0
                      for s in SUIT_ORDER], reverse=True)
    if lengths == [4, 3, 3, 3]:
        total += 0.5
    return round(total, 1)

# ---------------------------------------------------------------------------
# DDS
# ---------------------------------------------------------------------------
def board_to_deal(board):
    pbn = "N:{} {} {} {}".format(
        board["north"], board["east"], board["south"], board["west"])
    return Deal(pbn)

def run_dds(board):
    if not DDS_AVAILABLE:
        return None
    try:
        deal  = board_to_deal(board)
        table = calc_dd_table(deal)
        result = {pl: {} for pl in PLAYER_MAP}
        txt = str(table)
        sym_map = {"♣":"C","♦":"D","♥":"H","♠":"S","NT":"NT",
                   "C":"C","D":"D","H":"H","S":"S"}
        segments = txt.strip().split(";")
        dn_order = [sym_map.get(s.strip()) for s in segments[0].split(",")]
        dn_order = [d for d in dn_order if d]
        for seg in segments[1:]:
            if ":" not in seg:
                continue
            pl, vals_str = seg.split(":", 1)
            pl = pl.strip()
            if pl not in result:
                continue
            vals = vals_str.split(",")
            for i, dn in enumerate(dn_order):
                if i < len(vals):
                    try:
                        result[pl][dn] = int(vals[i].strip())
                    except ValueError:
                        result[pl][dn] = 0
        for pl in result:
            for dn in DENOM_MAP:
                result[pl].setdefault(dn, 0)
        has_data = any(result[p][d] > 0 for p in result for d in result[p])
        return result if has_data else None
    except Exception:
        return None

def optimum_contract(dds_table, vul):
    if not dds_table:
        return None
    ns_vul = vul in ("NS", "All")
    ew_vul = vul in ("EW", "All")
    def contract_score(level, denom, side, tricks_made):
        is_vul = ns_vul if side == "NS" else ew_vul
        if denom == "NT":     trick_score = 40 + 30 * (level - 1)
        elif denom in ("S","H"): trick_score = 30 * level
        else:                 trick_score = 20 * level
        overtricks = tricks_made - (6 + level)
        if overtricks < 0:
            return overtricks * (100 if is_vul else 50)
        score = trick_score
        if trick_score >= 100: score += 500 if is_vul else 300
        else: score += 50
        if level == 6: score += 750 if is_vul else 500
        elif level == 7: score += 1500 if is_vul else 1000
        ot_val = 30 if denom in ("NT","S","H") else 20
        score += overtricks * ot_val
        return score
    denom_disp = {"NT":"NT","S":"♠","H":"♥","D":"♦","C":"♣"}
    best_score, best_side, best_str, best_val = -9999, "NS", "PASS", 0
    for level in range(1, 8):
        for dn in ["NT","S","H","D","C"]:
            for side, players in [("NS",["N","S"]),("EW",["E","W"])]:
                best_tricks = max(dds_table.get(p,{}).get(dn,0) for p in players)
                sc = contract_score(level, dn, side, best_tricks)
                if sc > best_score:
                    best_score = sc; best_side = side
                    best_str = str(level) + denom_disp[dn]; best_val = sc
    sign = "+" if best_val >= 0 else ""
    return (best_side, best_str, sign + str(best_val))

# ---------------------------------------------------------------------------
# PBN parser
# ---------------------------------------------------------------------------
def parse_pbn(pbn_text):
    text = pbn_text
    deal_positions = [(m.start(), m.group(1))
                      for m in re.finditer(r'\[Deal\s+"([^"]+)"', text, re.IGNORECASE)]
    if not deal_positions:
        return []
    def tag_in(chunk, *names):
        for name in names:
            m = re.search(r'\[' + name + r'\s+"([^"]*)"', chunk, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None
    vul_map = {
        "NONE":"None","-":"None","0":"None",
        "NS":"NS","N-S":"NS","N/S":"NS",
        "EW":"EW","E-W":"EW","E/W":"EW",
        "ALL":"All","BOTH":"All","B":"All","BO":"All",
    }
    block_boundaries = []
    for idx, (pos, _) in enumerate(deal_positions):
        blk_start = 0 if idx == 0 else deal_positions[idx-1][0]
        blk_end   = (deal_positions[idx+1][0] if idx+1 < len(deal_positions) else len(text))
        block_boundaries.append((blk_start, blk_end))
    boards = []
    for idx, (pos, deal_str) in enumerate(deal_positions):
        blk_start, blk_end = block_boundaries[idx]
        chunk = text[blk_start:blk_end]
        prefix = deal_str.upper()
        if prefix.startswith("N:"):
            hands_str = deal_str[2:]
            parts = hands_str.split()
        else:
            continue
        if len(parts) < 4:
            continue
        north, east, south, west = parts[0], parts[1], parts[2], parts[3]
        board_num_str = tag_in(chunk, "Board", "BoardNum", "BordNr")
        try:
            board_num = int(board_num_str) if board_num_str else idx + 1
        except ValueError:
            board_num = idx + 1
        vul_raw = (tag_in(chunk, "Vulnerable", "Vul") or "None").upper().strip()
        vul     = vul_map.get(vul_raw, "None")
        dealer  = (tag_in(chunk, "Dealer") or "N").upper().strip()
        boards.append({
            "board": board_num, "north": north, "east": east,
            "south": south, "west": west, "vul": vul, "dealer": dealer,
        })
    return boards

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def fetch_pbn_from_url(page_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    candidate = page_url.rstrip("/") + "/pbn"
    try:
        r = requests.get(candidate, headers=headers, timeout=20)
        if r.ok and len(r.content) > 100:
            for enc in ("utf-8", "windows-1253", "iso-8859-7"):
                try:
                    return r.content.decode(enc)
                except Exception:
                    pass
            return r.text
    except Exception:
        pass
    # Fallback: scan page for PBN link
    try:
        r = requests.get(page_url, headers=headers, timeout=20)
        r.raise_for_status()
        for link in re.findall(r'href=["\']([^"\']+)["\']', r.text, re.IGNORECASE):
            if "pbn" in link.lower():
                pbn_url = (link if link.startswith("http")
                           else page_url.rstrip("/") + "/" + link.lstrip("/"))
                r2 = requests.get(pbn_url, headers=headers, timeout=20)
                r2.raise_for_status()
                for enc in ("utf-8", "windows-1253", "iso-8859-7"):
                    try:
                        return r2.content.decode(enc)
                    except Exception:
                        pass
                return r2.text
    except Exception:
        pass
    return None

def scrape_tournament_info(page_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(page_url, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception:
        return ""
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            text = r.content.decode(enc); break
        except Exception:
            text = r.text
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"&nbsp;", " ", plain)
    plain = re.sub(r"&amp;",  "&", plain)
    plain = re.sub(r"&#[0-9]+;", "", plain)
    plain = re.sub(r"[ \t]+", " ", plain).replace("\xa0", " ")
    date_str = ""
    m = re.search(r"Ημερομηνία\s*([0-9]{1,2}[/\-\.][0-9]{1,2}[/\-\.][0-9]{2,4})", plain)
    if m:
        date_str = m.group(1).strip()
    else:
        m = re.search(r"([0-9]{2}/[0-9]{2}/[0-9]{4})", plain)
        if m:
            date_str = m.group(1)
    name_str = ""
    m = re.search(r"<title[^>]*>([^<]+)</title>", text, re.IGNORECASE)
    if m:
        name_str = m.group(1).strip()
    parts  = [p for p in [name_str, date_str] if p]
    return "  |  ".join(parts)

def find_card_url(page_url, reg_number):
    headers_http = {"User-Agent": "Mozilla/5.0"}
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            r = requests.get(page_url, headers=headers_http, timeout=20)
            text = r.content.decode(enc); break
        except Exception:
            text = r.text
    pattern = (r'aria-label="[^"]*' + re.escape(str(reg_number)) +
               r'[^"]*"\s+href="([^"]+/card/\d+)"')
    m = re.search(pattern, text)
    if m:
        href = m.group(1)
        if href.startswith("http"):
            return href
        base = re.match(r'(https?://[^/]+)', page_url)
        return (base.group(1) if base else "") + href
    idx = text.find(str(reg_number))
    if idx >= 0:
        snippet = text[max(0, idx-300):idx+300]
        m2 = re.search(r'href="(/results/[^"]+/card/\d+)"', snippet)
        if m2:
            base = re.match(r'(https?://[^/]+)', page_url)
            return (base.group(1) if base else "") + m2.group(1)
    return None

def scrape_pair_results(card_url, page_url):
    suit_map = {"icon-spade":"♠","icon-heart":"♥","icon-diamond":"♦","icon-club":"♣"}
    headers_http = {"User-Agent": "Mozilla/5.0"}
    if not card_url.startswith("http"):
        base = re.match(r'(https?://[^/]+)', page_url)
        card_url = (base.group(1) if base else "") + card_url
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            r = requests.get(card_url, headers=headers_http, timeout=20)
            text = r.content.decode(enc); break
        except Exception:
            text = r.text
    for cls, sym in suit_map.items():
        text = re.sub(r'<i[^>]*class="[^"]*' + cls + r'[^"]*"[^>]*>', sym, text)
        text = re.sub(r'<i[^>]*' + cls + r'[^>]*>', sym, text)
    tbl_m = re.search(r'<table[^>]*class="[^"]*results card pairs[^"]*"[^>]*>',
                      text, re.IGNORECASE)
    if not tbl_m:
        return {}
    tbl_start = tbl_m.start()
    tbl_end   = text.find('</table>', tbl_start)
    tbl       = text[tbl_start:tbl_end+8] if tbl_end >= 0 else text[tbl_start:]
    rows      = re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.DOTALL | re.IGNORECASE)
    def cell_text(td_html):
        s = re.sub(r'<[^>]+>', ' ', td_html)
        s = re.sub(r'&nbsp;', ' ', s)
        s = re.sub(r'&amp;', '&', s)
        s = re.sub(r'&#[0-9]+;', '', s)
        return re.sub(r'\s+', ' ', s).strip()
    results = {}
    for row in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        if len(tds) < 5:
            continue
        try:
            board_num, bnum_idx = None, None
            for _bi, _td in enumerate(tds):
                if _bi < 2: continue
                _v = cell_text(_td).strip()
                if _v.isdigit() and 1 <= int(_v) <= 99:
                    board_num = int(_v); bnum_idx = _bi; break
            if board_num is None:
                continue
            bi = bnum_idx
            def first_a_text(td_html):
                m = re.search(r'<a[^>]*>(.*?)</a>', td_html, re.DOTALL)
                return cell_text(m.group(1)) if m else cell_text(td_html)
            # Round is in the column just before board number (bi-1)
            round_val = cell_text(tds[bi-1]) if bi >= 1 else ""
            opp1     = first_a_text(tds[bi+2]) if len(tds) > bi+2 else ""
            opp2     = first_a_text(tds[bi+4]) if len(tds) > bi+4 else ""
            contract = cell_text(tds[bi+5]) if len(tds) > bi+5 else ""
            declarer = cell_text(tds[bi+6]) if len(tds) > bi+6 else ""
            lead     = cell_text(tds[bi+7]) if len(tds) > bi+7 else ""
            score    = cell_text(tds[bi+8]) if len(tds) > bi+8 else ""
            pct      = cell_text(tds[bi+9]) if len(tds) > bi+9 else ""
            if board_num not in results:
                results[board_num] = {
                    "round": round_val,
                    "opponent1": opp1, "opponent2": opp2,
                    "contract": contract, "declarer": declarer,
                    "lead": lead, "score": score, "pct": pct,
                }
        except Exception:
            continue
    return results

# ---------------------------------------------------------------------------
# Tournament scraper
# ---------------------------------------------------------------------------
def _clean_html(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'&nbsp;', ' ', s)
    s = re.sub(r'&amp;', '&', s)
    s = re.sub(r'&#[0-9]+;', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def _decode(r):
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            return r.content.decode(enc)
        except Exception:
            pass
    return r.text

def scrape_tournament_list(max_page=6):
    base    = "https://hellasbridge.org/results"
    headers = {"User-Agent": "Mozilla/5.0"}
    tournaments = []
    seen_urls   = set()
    for page in range(1, max_page + 1):
        url = base if page == 1 else "{}?page={}".format(base, page)
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
        except Exception:
            continue
        text = _decode(r)
        row_blocks  = re.findall(r'<tr[^>]*>.*?</tr>', text, re.DOTALL | re.IGNORECASE)
        row_blocks += re.findall(r'<li[^>]*>.*?</li>', text, re.DOTALL | re.IGNORECASE)
        for block in row_blocks:
            m_link = re.search(
                r'href=["\'](/results/(\d+)(?:[^"\']*)?)["\'][^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE)
            if not m_link:
                continue
            tid, raw_title = m_link.group(2), m_link.group(3)
            base_url = "https://hellasbridge.org/results/" + tid
            if base_url in seen_urls:
                continue
            seen_urls.add(base_url)
            title = _clean_html(raw_title)
            if not title:
                continue
            date_str = ""
            m_date = re.search(
                r'(\b\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}\b'
                r'|\b\d{4}[/\-\.]\d{2}[/\-\.]\d{2}\b)',
                _clean_html(block))
            if m_date:
                date_str = m_date.group(1)
            club = ""
            tds = re.findall(r'<td[^>]*>(.*?)</td>', block, re.DOTALL | re.IGNORECASE)
            for td in tds:
                candidate = _clean_html(td)
                if (not candidate or candidate == title
                        or re.fullmatch(r'[\d/\-\. ]+', candidate)
                        or len(candidate) < 3):
                    continue
                club = candidate; break
            tournaments.append({"title": title, "url": base_url,
                                 "date": date_str, "club": club})
    return tournaments

def parse_date_str(date_str):
    if not date_str:
        return None
    for fmt in ("%d/%m/%Y","%d-%m-%Y","%d.%m.%Y",
                "%Y/%m/%d","%Y-%m-%d","%Y.%m.%d",
                "%d/%m/%y","%d-%m-%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            pass
    return None

# ---------------------------------------------------------------------------
# Rendering (identical logic to desktop)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# RENDER BOARD
# ---------------------------------------------------------------------------
def render_board(board, dds_table, cell_w, cell_h, caption=""):
    """
    Layout (matching reference images):
      - Board number: top-left, large font
      - Optimum contract: top-right, above East's cards
      - Compass hands: all left-aligned from their anchor x
          North: centred above box
          South: centred below box
          West:  left edge starts from a fixed left margin
          East:  left edge starts from right side of box
      - HCP cross: bottom-left area, in cross format
          (N on top, W left, E right, S bottom — centred on a point
           to the left of South's cards)
      - DD table: right side, two mini-tables (N/S, E/W)
    """
    img  = Image.new("RGB", (cell_w, cell_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    PAD = max(4, cell_w // 70)

    # ── Fonts ────────────────────────────────────────────────────────────
    fs_hand  = max(10, cell_h // 19)
    fs_cmp   = max(9,  cell_h // 22)
    fs_bnum  = max(13, cell_h // 11)   # large board number
    fs_hcp   = max(9,  cell_h // 22)
    fs_opt   = max(9,  cell_h // 24)
    fs_th    = max(8,  cell_h // 26)
    fs_tv    = max(9,  cell_h // 24)

    fhand  = make_font(fs_hand)
    fcmp   = make_bold_font(fs_cmp)
    fbnum  = make_font(fs_bnum)
    fhcp   = make_font(fs_hcp)
    fhcpb  = make_bold_font(fs_hcp + 2)   # bold HCP / optimum font
    fopt   = make_font(fs_opt)
    fth    = make_font(fs_th)
    ftv    = make_font(fs_tv)

    lh  = th(fhand) + 2    # line height for card lines
    hbh = 4 * lh           # full hand block height

    # ── Layout zones ─────────────────────────────────────────────────────
    # Right portion = DD table
    compass_w = int(cell_w * 0.63)
    tbl_x     = compass_w + PAD
    tbl_w     = cell_w - tbl_x - PAD

    # Centre compass box — vertically centred, horizontally centred in
    # compass area but shifted right a bit to leave room for West hand
    box_size = max(46, min(compass_w // 3, cell_h // 4))   # slightly larger box
    box_x    = (compass_w - box_size) // 2
    # Shift entire body (compass + hands + tables) 0.15cm down
    _body_shift = round(1.5 * 1240 / 210) + round(2.0 * 1240 / 210)   # ~9 + 12 = 21 px (extra 0.2cm down)
    box_y    = (cell_h - box_size) // 2 + _body_shift
    cx       = box_x + box_size // 2
    cy       = box_y + box_size // 2
    bx, by, bx2, by2 = box_x, box_y, box_x + box_size, box_y + box_size

    # Hand anchor positions
    # North: centred above box
    north_y = box_y - hbh - PAD * 2
    # South: centred below box
    south_y = box_y + box_size + PAD * 2
    # West/East: vertically centred beside box
    side_y  = box_y + (box_size - hbh) // 2

    # ── Vulnerability triangles ───────────────────────────────────────────
    vul    = board.get("vul", "None")
    ns_vul = vul in ("NS", "All")
    ew_vul = vul in ("EW", "All")

    # Triangle fill: red if vulnerable, white if not
    ns_fill = (210, 50, 50) if ns_vul else (255, 255, 255)
    ew_fill = (210, 50, 50) if ew_vul else (255, 255, 255)

    draw.polygon([(bx, by),  (bx2, by),  (cx, cy)], fill=ns_fill)  # N
    draw.polygon([(bx, by2), (bx2, by2), (cx, cy)], fill=ns_fill)  # S
    draw.polygon([(bx, by),  (bx, by2),  (cx, cy)], fill=ew_fill)  # W
    draw.polygon([(bx2, by), (bx2, by2), (cx, cy)], fill=ew_fill)  # E
    # No outline on the box (as requested)

    # Compass letters (N/W/E/S) — black text inside the box
    # Dealer gets a circle: red (filled) if that axis is vulnerable, white if not
    dealer = board.get("dealer", "N").upper()
    dealer_axis_vul = {
        "N": ns_vul, "S": ns_vul,
        "E": ew_vul, "W": ew_vul,
    }

    # Inset letters further from the edge so the dealer disc clears the border
    _cmp_inset = max(2, box_size // 10)
    compass_positions = [
        ("N", cx,              by + _cmp_inset,                    "c"),
        ("W", bx + _cmp_inset, cy - th(fcmp) // 2,                 "l"),
        ("E", bx2 - _cmp_inset, cy - th(fcmp) // 2,                "r"),
        ("S", cx,              by2 - th(fcmp) - _cmp_inset,        "c"),
    ]
    for label, lx, ly, anchor in compass_positions:
        lw = tw(label, fcmp)
        lh_cmp = th(fcmp)
        if anchor == "c":
            tx_ = lx - lw // 2
        elif anchor == "r":
            tx_ = lx - lw
        else:
            tx_ = lx

        if label == dealer:
            circ_r = max(lw, lh_cmp) // 2 + 4
            circ_cx = tx_ + lw // 2
            circ_cy = ly + lh_cmp // 2
            is_vul = dealer_axis_vul.get(label, False)
            # Red disc when NON-vulnerable, white disc when vulnerable
            circ_fill = (255, 255, 255) if is_vul else (210, 50, 50)
            # Supersample 4x for smooth anti-aliased circle
            ss = 4
            disc_d = (circ_r * 2 + 2) * ss
            disc_img = Image.new("RGBA", (disc_d, disc_d), (0, 0, 0, 0))
            disc_draw = ImageDraw.Draw(disc_img)
            disc_draw.ellipse([0, 0, disc_d - 1, disc_d - 1],
                              fill=circ_fill + (255,))
            disc_img = disc_img.resize(
                (circ_r * 2 + 2, circ_r * 2 + 2), Image.LANCZOS)
            img.paste(disc_img,
                      (circ_cx - circ_r - 1, circ_cy - circ_r - 1),
                      disc_img)
            # Black letter on white disc, white letter on red disc
            letter_color = (0, 0, 0) if is_vul else (255, 255, 255)
        else:
            letter_color = (0, 0, 0)

        draw.text((tx_, ly), label, fill=letter_color, font=fcmp)

    # ── Draw hand helper (always left-aligned from x0) ────────────────────
    def draw_hand(hand_str, x0, y0):
        """Draw 4 suit lines left-aligned from x0. Returns block width."""
        hand = parse_hand(hand_str)
        y    = y0
        max_w = 0
        for suit in SUIT_ORDER:
            sym   = SUIT_SYMBOL[suit]
            ranks = hand[suit] or "-"
            sw_   = tw(sym, fhand)
            cw_   = tw(" " + ranks, fhand)
            draw.text((x0, y), sym, fill=SUIT_COLOR[suit], font=fhand)
            draw.text((x0 + sw_, y), " " + ranks, fill=(0, 0, 0), font=fhand)
            max_w = max(max_w, sw_ + cw_)
            y += lh
        return max_w

    # North — left-aligned, block centred horizontally in compass area
    nh  = parse_hand(board["north"])
    nw_ = max(tw(SUIT_SYMBOL[s] + " " + (nh[s] or "-"), fhand) for s in SUIT_ORDER)
    nx  = (compass_w - nw_) // 2
    draw_hand(board["north"], nx, north_y)

    # South — left-aligned, block centred horizontally in compass area
    sh  = parse_hand(board["south"])
    sw_ = max(tw(SUIT_SYMBOL[s] + " " + (sh[s] or "-"), fhand) for s in SUIT_ORDER)
    sx_ = (compass_w - sw_) // 2
    draw_hand(board["south"], sx_, south_y)

    # West — left-aligned, anchored so rightmost card column ends at bx-PAD
    wh  = parse_hand(board["west"])
    ww_ = max(tw(SUIT_SYMBOL[s] + " " + (wh[s] or "-"), fhand) for s in SUIT_ORDER)
    wx_ = bx - PAD - ww_
    wx_ = max(PAD, wx_)           # clamp to left edge
    draw_hand(board["west"], wx_, side_y)

    # East — left-aligned from right edge of box
    ex_ = bx2 + PAD
    ew_ = draw_hand(board["east"], ex_, side_y)

    # ── Board number: top-left, bold italic, offset 8pts down+right ────────
    fbnum_calibri = make_bold_italic_font(44)
    bnum_str = str(board["board"])
    draw.text((PAD + 8, PAD + 23), bnum_str, fill=(0, 0, 0), font=fbnum_calibri)

    # ── HCP + KRHCP rectangle ────────────────────────────────────────────────
    hcp_n  = calc_hcp(board["north"]);  kr_n = calc_krhcp(board["north"])
    hcp_s  = calc_hcp(board["south"]);  kr_s = calc_krhcp(board["south"])
    hcp_e  = calc_hcp(board["east"]);   kr_e = calc_krhcp(board["east"])
    hcp_w  = calc_hcp(board["west"]);   kr_w = calc_krhcp(board["west"])
    hcol   = (50, 50, 50)
    krcol  = (80, 80, 180)    # slightly blue for KRHCP
    hcph   = th(fhcpb)
    fkr    = make_font(max(7, fs_hcp - 1))   # smaller font for KRHCP in parens

    _ppm      = 1240 / 210
    _hcp_w    = round(8.3  * _ppm)
    _hcp_h    = round(7.5  * _ppm)
    _diag_off = round(4.0  * _ppm / 1.4142)

    def draw_hcp_kr(x, y, hcp_val, kr_val, anchor="c"):
        """Draw  HCP (KR)  with anchor: c=centre, l=left, r=right."""
        hstr = str(hcp_val)
        kstr = "({})".format(kr_val)
        hw_  = tw(hstr, fhcpb)
        kw_  = tw(kstr, fkr)
        gap  = 2
        total_w = hw_ + gap + kw_
        if anchor == "c":
            sx = x - total_w // 2
        elif anchor == "r":
            sx = x - total_w
        else:
            sx = x
        draw.text((sx, y), hstr, fill=hcol, font=fhcpb)
        draw.text((sx + hw_ + gap, y + hcph // 2 - th(fkr) // 2),
                  kstr, fill=krcol, font=fkr)

    # (HCP draw calls follow after DD geometry where _hcp_cx etc. are defined)

    # ── DD table geometry (computed always so opt text can reference it) ───────
    DD_COL_SUITS   = ["C", "D", "H", "S", "NT"]
    DD_ROW_PLAYERS = ["N", "S", "E", "W"]
    rh      = max(15, th(ftv) + 6)
    hdr_h   = rh
    lbl_cw  = max(18, tw("W", fth) + 10)
    suit_cw = max(20, tw("13", ftv) + 10)
    tbl_total_w = lbl_cw + 5 * suit_cw
    tbl_total_h = hdr_h + len(DD_ROW_PLAYERS) * rh
    _ppm_dt  = 1240 / 210
    _dt_off  = round(3.5 * _ppm_dt / 1.4142)
    dt_x = bx2 + _dt_off
    dt_y = by2 + _dt_off
    dt_x = max(PAD, min(dt_x, cell_w - PAD - tbl_total_w))
    dt_y = max(PAD, min(dt_y, cell_h - PAD - tbl_total_h))   # back to original vertical position
    _right_shift = round(5 * 1240 / 210) + round(3 * 1240 / 210)   # 0.8 cm right shift ~48 px
    dt_x += _right_shift

    # ── Optimum contract: just above DD table, right-aligned ──
    opt = optimum_contract(dds_table, vul)
    tbl_right = dt_x + tbl_total_w
    if opt:
        side, contract, score = opt
        opt_text = "Opt: {} {} {}".format(side, contract, score)
        opt_y = dt_y - th(fopt) - PAD
        opt_w = tw(opt_text, fopt)
        half_char = tw(" ", fopt)
        draw_suit_text(draw, tbl_right - opt_w - half_char, opt_y, opt_text, fopt,
                       default_color=(40, 40, 40))

    # ── HCP box: at north_y (where opt used to be), right-aligned to DD table ──
    _hcp_extra_left = round(10 * 1240 / 210)   # 1.0 cm in pixels ~59 px
    _hcp_rx2 = tbl_right
    _hcp_ry1 = north_y - 4                     # start at north_y, 4px enlarged upward
    _hcp_rx1 = _hcp_rx2 - _hcp_w - _hcp_extra_left
    _hcp_ry2 = _hcp_ry1 + _hcp_h + 20         # enlarged 20px total
    _hcp_cx  = (_hcp_rx1 + _hcp_rx2) // 2
    _hcp_cy  = (_hcp_ry1 + _hcp_ry2) // 2

    # N — centre of top side
    draw_hcp_kr(_hcp_cx, _hcp_ry1 + 1, hcp_n, kr_n, anchor="c")
    # S — centre of bottom side
    draw_hcp_kr(_hcp_cx, _hcp_ry2 - hcph - 1, hcp_s, kr_s, anchor="c")
    # W — centre of left side
    draw_hcp_kr(_hcp_rx1 + 2, _hcp_cy - hcph // 2, hcp_w, kr_w, anchor="l")
    # E — centre of right side
    draw_hcp_kr(_hcp_rx2 - 2, _hcp_cy - hcph // 2, hcp_e, kr_e, anchor="r")

    # ── DD table: 5 cols (♣ ♦ ♥ ♠ NT) x 4 rows (N S E W) ───────────────────
    if dds_table:

        # Header row: suit symbols
        draw.rectangle([dt_x, dt_y, dt_x + tbl_total_w - 1, dt_y + hdr_h - 1],
                        fill=(220, 220, 240))
        for ci, suit in enumerate(DD_COL_SUITS):
            cx_ = dt_x + lbl_cw + ci * suit_cw
            if suit == "NT":
                sym, sc = "NT", (0, 0, 100)
            else:
                sym, sc = SUIT_SYMBOL[suit], SUIT_COLOR[suit]
            draw.text((cx_ + (suit_cw - tw(sym, fth)) // 2, dt_y + 1),
                      sym, fill=sc, font=fth)

        # Data rows
        game_tricks = {"NT": 9, "S": 10, "H": 10, "D": 11, "C": 11}
        for ri, player in enumerate(DD_ROW_PLAYERS):
            ry  = dt_y + hdr_h + ri * rh
            rbg = (245, 245, 255) if ri % 2 == 0 else (255, 255, 255)
            draw.rectangle([dt_x, ry, dt_x + tbl_total_w - 1, ry + rh - 1],
                            fill=rbg)
            draw.text((dt_x + (lbl_cw - tw(player, fth)) // 2, ry + 1),
                      player, fill=(0, 0, 100), font=fth)
            for ci, suit in enumerate(DD_COL_SUITS):
                tricks = dds_table.get(player, {}).get(suit, 0)
                level  = tricks - 6          # convert tricks to contract level
                if level <= 0:
                    continue                  # show nothing if can't make 1-level
                vs  = str(level)
                vx  = dt_x + lbl_cw + ci * suit_cw
                if level == 7:
                    vc = (140, 0, 140)       # grand slam
                elif level == 6:
                    vc = (0, 100, 0)         # small slam
                elif level >= {"NT":3,"S":4,"H":4,"D":5,"C":5}.get(suit,4):
                    vc = (0, 0, 180)         # game
                else:
                    vc = (0, 0, 0)           # partial
                draw.text((vx + (suit_cw - tw(vs, ftv)) // 2, ry + 1),
                          vs, fill=vc, font=ftv)

        # Table border + grid lines
        draw.rectangle([dt_x, dt_y,
                         dt_x + tbl_total_w - 1,
                         dt_y + tbl_total_h - 1],
                        outline=(150, 150, 150), width=1)
        draw.line([(dt_x + lbl_cw, dt_y),
                   (dt_x + lbl_cw, dt_y + tbl_total_h - 1)],
                  fill=(150, 150, 150), width=1)
        draw.line([(dt_x, dt_y + hdr_h),
                   (dt_x + tbl_total_w - 1, dt_y + hdr_h)],
                  fill=(150, 150, 150), width=1)

    # ── Caption strip at very top of board (two lines) ─────────────────────
    if caption:
        fcap  = make_bold_italic_font(max(7, fs_th + 4))
        cap_lh = th(fcap) + 1
        RED   = (200, 0, 0)
        BLACK = (40, 40, 40)
        lines = caption.split("\n")
        max_w = cell_w - 2 * PAD

        def draw_fitted_line(line, cap_y, line_max_w=None):
            """Render line at full font size; squish horizontally if too wide."""
            if line_max_w is None:
                line_max_w = max_w
            line_h = th(fcap)
            # Render onto a temporary wide image
            tmp_w = max(cell_w * 3, sum(tw(ch, fcap) for ch in line) + 4)
            tmp = Image.new("RGBA", (tmp_w, line_h + 4), (255, 255, 255, 0))
            tdraw = ImageDraw.Draw(tmp)
            x_ = 0
            for ch in line:
                color = RED if ch in ("♥", "♦") else BLACK
                tdraw.text((x_, 0), ch, fill=color + (255,), font=fcap)
                x_ += tw(ch, fcap)
            text_w = x_
            # Crop to actual text width
            tmp = tmp.crop((0, 0, text_w, line_h + 4))
            # Squish horizontally if wider than line_max_w, else centre as-is
            if text_w > line_max_w:
                tmp = tmp.resize((line_max_w, line_h + 4), Image.LANCZOS)
                paste_x = (cell_w - line_max_w) // 2
            else:
                paste_x = max(PAD, (cell_w - text_w) // 2)
            img.paste(tmp, (paste_x, cap_y), tmp)

        for li, line in enumerate(lines):
            # Line 0 (names): always leave 10% margin each side (80% usable width)
            # Line 1 (contract etc): use full width minus PAD
            line_max_w = int(cell_w * 0.80) if li == 0 else max_w
            draw_fitted_line(line, 2 + li * cap_lh, line_max_w)

    # Outer border
    draw.rectangle([0, 0, cell_w - 1, cell_h - 1],
                   outline=(160, 160, 160), width=1)

    return img


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

def _make_clubs_icon():
    """Create a ♣ favicon as a PIL Image for st.set_page_config."""
    img = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    fnt = None
    for font_path in [FONT_BOLD_PATH, FONT_PATH]:
        try:
            fnt = ImageFont.truetype(str(font_path), 56)
            break
        except: pass
    if fnt is None: fnt = ImageFont.load_default()
    
    symbol = "♣"
    try:
        bbox = d.textbbox((0, 0), symbol, font=fnt)
        gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        ox, oy = (64 - gw) // 2 - bbox[0], (64 - gh) // 2 - bbox[1]
    except: ox, oy = 4, 4
    
    # Σχεδίαση μαύρου σπαθιού
    d.text((ox, oy), symbol, fill=(0, 0, 0, 255), font=fnt)
    return img

st.set_page_config(
    page_title="Bridge Hand Records",
    page_icon=_make_clubs_icon(), # Χρήση της PIL εικόνας αντί για emoji
    layout="centered"
)


# ── Session state init ───────────────────────────────────────────────────────
for key, default in [
    ("step", "disclaimer"),
    ("reg", None),
    ("tournaments", None),
    ("chosen_url", None),
    ("chosen_title", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── STEP 1: Disclaimer ───────────────────────────────────────────────────────
if st.session_state.step == "disclaimer":
    st.title("🃏 Bridge Hand Records")
    st.markdown("### Όροι Χρήσης Εφαρμογής")
    st.info(
        "Η χρήση αυτής της εφαρμογής παρέχεται δωρεάν, αποκλειστικά για "
        "ενημερωτικούς και βοηθητικούς σκοπούς.\n\n"
        "Δεν παρέχεται καμία εγγύηση, ρητή ή σιωπηρή, σχετικά με την ακρίβεια, "
        "την πληρότητα ή την ορθότητα των πληροφοριών που παράγονται από αυτή "
        "ούτε για την αδιάλειπτη λειτουργία της. Ο δημιουργός δεν φέρει καμία "
        "ευθύνη για τυχόν λάθη, παραλείψεις ή για οποιαδήποτε χρήση ή ερμηνεία "
        "του περιεχομένου από τρίτους.\n\n"
        "Σε καμία περίπτωση το παρόν υλικό δεν αντικαθιστά την πληροφορία που "
        "υπάρχει στον επίσημο ιστότοπο της ΕΟΜ ούτε τα έγγραφα που διανέμονται "
        "από τους διαιτητές κατά τη διάρκεια των αγώνων. Η πληροφορία στον "
        "ιστότοπο της ΕΟΜ και επίσημα έγγραφα αποτελούν τη μοναδική έγκυρη "
        "πηγή πληροφόρησης.\n\n"
        "Με την πρόσβαση και χρήση του περιεχομένου, αποδέχεστε τους παραπάνω όρους."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✔ Συμφωνώ", use_container_width=True, type="primary"):
            st.session_state.step = "auth"
            st.rerun()
    with col2:
        if st.button("✖ Δε συμφωνώ", use_container_width=True):
            st.error("Η εφαρμογή απαιτεί αποδοχή των όρων χρήσης.")
            st.stop()

# ── STEP 2: Authorization ────────────────────────────────────────────────────
elif st.session_state.step == "auth":
    st.title("🃏 Bridge Hand Records")
    st.markdown("### Εξουσιοδότηση")
    reg = st.text_input("Αριθμός Μητρώου παίκτη", placeholder="π.χ. 15672")
    if st.button("✔ Είσοδος", type="primary"):
        if not reg.strip():
            st.error("⚠ Ο αριθμός μητρώου δεν μπορεί να είναι κενός.")
        else:
            valid = load_valid_numbers()
            if reg.strip() not in valid:
                st.error(
                    "⛔ Η χρήση της εφαρμογής επιτρέπεται μόνο σε εξουσιοδοτημένους "
                    "χρήστες. Για εξουσιοδότηση επικοινωνήστε με το tak_0000@yahoo.com"
                )
                st.stop()
            else:
                st.session_state.reg  = reg.strip()
                st.session_state.step = "pick"
                st.rerun()


# ── STEP 3: Pick tournament (Fixed Transition) ──────────────────────────────
elif st.session_state.step == "pick":
    st.title("🃏 Bridge Hand Records")
    st.markdown("### Επιλογή Τουρνουά")

    if not st.session_state.tournaments:
        with st.spinner("Ανάκτηση δεδομένων..."):
            raw_t = scrape_tournament_list(max_page=4)
            # Φίλτρο 3 ημερών
            cutoff = date.today() - timedelta(days=3)
            st.session_state.tournaments = [
                t for t in raw_t 
                if parse_date_str(t.get("date","")) and parse_date_str(t.get("date","")) >= cutoff
            ]
            if not st.session_state.tournaments:
                st.session_state.tournaments = raw_t[:10] # Fallback αν το 3ήμερο είναι κενό

    import pandas as pd
    df = pd.DataFrame(st.session_state.tournaments)
    display_df = df[['date', 'club', 'title']].copy()
    display_df.columns = ['Ημερομηνία', 'Σωματείο', 'Τουρνουά']

    search_query = st.text_input("🔍 Αναζήτηση:", "")
    if search_query:
        mask = display_df.apply(lambda r: r.astype(str).str.contains(search_query, case=False).any(), axis=1)
        display_df = display_df[mask]

    # Πίνακας Επιλογής
    event = st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun"
    )

    if event.selection.rows:
        sel_idx = event.selection.rows[0]
        actual_idx = display_df.index[sel_idx]
        chosen = st.session_state.tournaments[actual_idx]
        
        st.success(f"📍 Επιλέχθηκε: **{chosen['title']}**")
        
        # ΕΔΩ ΕΙΝΑΙ Η ΔΙΟΡΘΩΣΗ: Αλλαγή σε "generate"
        if st.button("🚀 Δημιουργία PDF", type="primary"):
            st.session_state.chosen_url = chosen["url"]
            st.session_state.chosen_title = chosen["title"]
            st.session_state.step = "generate" # <--- Πρέπει να είναι "generate"
            st.rerun()

    if st.button("⬅ Πίσω"):
        st.session_state.tournaments = None
        st.session_state.step = "init"
        st.rerun()

# ── STEP 4: Generate PDF ─────────────────────────────────────────────────────
elif st.session_state.step == "generate":
    st.title("🃏 Bridge Hand Records")
    reg           = st.session_state.reg
    page_url      = st.session_state.chosen_url
    chosen_title  = st.session_state.chosen_title

    st.markdown("**Τουρνουά:** {}".format(chosen_title))
    st.markdown("**Αριθμός Μητρώου:** {}".format(reg))

    if not DDS_AVAILABLE:
        st.warning("⚠ Η βιβλιοθήκη DDS (endplay) δεν είναι διαθέσιμη. "
                   "Το PDF θα παραχθεί χωρίς double-dummy analysis.")

    if st.button("🚀 Δημιουργία PDF", type="primary"):
        with st.spinner("Φόρτωση PBN…"):
            pbn_text = fetch_pbn_from_url(page_url)
        if not pbn_text:
            st.error("Δεν υπάρχουν διανομές για το τουρνουά που επιλέξατε.")
            st.stop()

        boards = parse_pbn(pbn_text)
        if not boards:
            st.error("Δεν βρέθηκαν boards στο PBN αρχείο.")
            st.stop()

        with st.spinner("Αναζήτηση αποτελεσμάτων παίκτη…"):
            header      = scrape_tournament_info(page_url)
            card_url    = find_card_url(page_url, reg)
            pair_results = {}
            if card_url:
                pair_results = scrape_pair_results(card_url, page_url)
            else:
                st.error("Ο αθλητής δεν συμμετείχε στο τουρνουά που επιλέξατε.")
                if st.button("⬅ Επιστροφή στην εισαγωγή αριθμού μητρώου"):
                    st.session_state.step = "auth"
                    st.rerun()
                st.stop()

        progress_bar = st.progress(0, text="Rendering boards…")
        images = []
        for i, board in enumerate(boards):
            progress_bar.progress((i+1)/len(boards),
                                  text="Board {}/{}…".format(i+1, len(boards)))
            cell_w = (A4_W - 2*MARGIN - 2*PADDING) // COLS
            cell_h = (A4_H - 2*MARGIN - 2*PADDING) // ROWS
            img = render_board(board, cell_w, cell_h,
                               pair_results=pair_results, header=header)
            images.append(img)
        progress_bar.empty()

        with st.spinner("Συναρμολόγηση PDF…"):
            pdf_bytes = assemble_pages_to_bytes(
                images, cell_w, cell_h,
                header=header, pair_results=pair_results, boards=boards)

        safe_title = re.sub(r'[^\w]', '_', chosen_title)[:60] or "bridge"
        filename   = safe_title + ".pdf"

        st.success("✅ Το PDF είναι έτοιμο!")
        st.download_button(
            label="⬇ Λήψη PDF",
            data=pdf_bytes,
            file_name=filename,
            mime="application/pdf",
            use_container_width=True,
        )

    if st.button("← Πίσω στα τουρνουά"):
        st.session_state.step = "pick"
        st.rerun()
