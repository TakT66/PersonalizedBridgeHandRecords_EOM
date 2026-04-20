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
#VALID_FILE = Path(__file__).resolve().parent / "Valid_Registration_Numbers.txt"
#
#def load_valid_numbers():
#    if not VALID_FILE.exists():
#        return set()
#    with open(VALID_FILE, encoding="utf-8", errors="ignore") as f:
#        return {line.strip() for line in f if line.strip()}

def load_valid_numbers():
    try:
        # Διαβάζει τη λίστα απευθείας από τα Secrets του Streamlit
        # Επιστρέφει ένα set για πολύ γρήγορη αναζήτηση
        return set(st.secrets["ALLOWED_AM"])
    except Exception:
        # Αν ξεχάσεις να τα ορίσεις στα Secrets, επιστρέφει κενό σετ
        st.error("Critical Error: 'ALLOWED_AM' not found in Secrets.")
        return set()



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

def scrape_tournament_list(max_page=8):
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
def render_board(board, cell_w, cell_h, pair_results=None, header=""):
    dds_table = run_dds(board)
    opt       = optimum_contract(dds_table, board.get("vul","None")) if dds_table else None

    caption = ""
    if pair_results:
        pr = pair_results.get(board["board"])
        if pr:
            opp = " - ".join(filter(None, [pr.get("opponent1",""), pr.get("opponent2","")]))
            round_str = ("Γύρος " + pr["round"]) if pr.get("round") else ""
            first_parts = [p for p in [round_str, opp] if p]
            parts2 = ["  ".join(first_parts)] if first_parts else []
            detail = []
            if pr.get("contract"): detail.append(pr["contract"])
            if pr.get("declarer"): detail.append("από " + pr["declarer"])
            if pr.get("lead"):     detail.append("Lead: " + pr["lead"])
            if pr.get("score"):    detail.append(pr["score"])
            if pr.get("pct"):      detail.append(pr["pct"].rstrip("%") + "%")
            if detail:
                parts2.append("  ".join(detail))
            caption = "\n".join(parts2)

    img  = Image.new("RGB", (cell_w, cell_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    PAD  = max(4, cell_w // 70)

    fs_hand = max(10, cell_h // 19)
    fs_cmp  = max(9,  cell_h // 22)
    fs_hcp  = max(9,  cell_h // 22)
    fs_th   = max(8,  cell_h // 26)
    fs_tv   = max(9,  cell_h // 24)

    fhand = make_font(fs_hand)
    fcmp  = make_bold_font(fs_cmp)
    fhcpb = make_bold_font(fs_hcp + 2)
    fkr   = make_font(max(7, fs_hcp - 1))
    fth   = make_font(fs_th)
    ftv   = make_font(fs_tv)

    lh  = th(fhand) + 2
    hbh = 4 * lh

    compass_w = int(cell_w * 0.63)
    box_size  = max(46, min(compass_w // 3, cell_h // 4))
    box_x     = (compass_w - box_size) // 2
    _body_shift = round(1.5 * 1240 / 210) + round(2.0 * 1240 / 210)
    box_y = (cell_h - box_size) // 2 + _body_shift
    cx    = box_x + box_size // 2
    cy    = box_y + box_size // 2
    bx, by, bx2, by2 = box_x, box_y, box_x + box_size, box_y + box_size

    north_y = box_y - hbh - PAD * 2
    south_y = box_y + box_size + PAD * 2
    side_y  = box_y + (box_size - hbh) // 2

    vul    = board.get("vul", "None")
    ns_vul = vul in ("NS", "All")
    ew_vul = vul in ("EW", "All")
    ns_fill = (210, 50, 50) if ns_vul else (255, 255, 255)
    ew_fill = (210, 50, 50) if ew_vul else (255, 255, 255)

    draw.polygon([(bx, by),  (bx2, by),  (cx, cy)], fill=ns_fill)
    draw.polygon([(bx, by2), (bx2, by2), (cx, cy)], fill=ns_fill)
    draw.polygon([(bx, by),  (bx, by2),  (cx, cy)], fill=ew_fill)
    draw.polygon([(bx2, by), (bx2, by2), (cx, cy)], fill=ew_fill)

    dealer = board.get("dealer", "N").upper()
    dealer_axis_vul = {"N": ns_vul, "S": ns_vul, "E": ew_vul, "W": ew_vul}
    _cmp_inset = max(2, box_size // 10)
    compass_positions = [
        ("N", cx,              by + _cmp_inset,             "c"),
        ("W", bx + _cmp_inset, cy - th(fcmp) // 2,          "l"),
        ("E", bx2 - _cmp_inset, cy - th(fcmp) // 2,         "r"),
        ("S", cx,              by2 - th(fcmp) - _cmp_inset, "c"),
    ]
    for label, lx, ly, anchor in compass_positions:
        lw = tw(label, fcmp); lh_cmp = th(fcmp)
        tx_ = lx - lw//2 if anchor=="c" else (lx - lw if anchor=="r" else lx)
        if label == dealer:
            circ_r = max(lw, lh_cmp)//2 + 4
            circ_cx, circ_cy = tx_ + lw//2, ly + lh_cmp//2
            is_vul = dealer_axis_vul.get(label, False)
            circ_fill = (255,255,255) if is_vul else (210,50,50)
            ss = 4; disc_d = (circ_r*2+2)*ss
            disc_img = Image.new("RGBA", (disc_d, disc_d), (0,0,0,0))
            disc_draw = ImageDraw.Draw(disc_img)
            disc_draw.ellipse([0, 0, disc_d-1, disc_d-1], fill=circ_fill+(255,))
            disc_img = disc_img.resize((circ_r*2+2, circ_r*2+2), Image.LANCZOS)
            img.paste(disc_img, (circ_cx-circ_r-1, circ_cy-circ_r-1), disc_img)
            letter_color = (0,0,0) if is_vul else (255,255,255)
        else:
            letter_color = (0,0,0)
        draw.text((tx_, ly), label, fill=letter_color, font=fcmp)

    def draw_hand(hand_str, x0, y0):
        hand = parse_hand(hand_str); y = y0; max_w = 0
        for suit in SUIT_ORDER:
            sym = SUIT_SYMBOL[suit]; ranks = hand[suit] or "-"
            sw_ = tw(sym, fhand); cw_ = tw(" "+ranks, fhand)
            draw.text((x0, y), sym, fill=SUIT_COLOR[suit], font=fhand)
            draw.text((x0+sw_, y), " "+ranks, fill=(0,0,0), font=fhand)
            max_w = max(max_w, sw_+cw_); y += lh
        return max_w

    nh  = parse_hand(board["north"])
    nw_ = max(tw(SUIT_SYMBOL[s]+" "+(nh[s] or "-"), fhand) for s in SUIT_ORDER)
    draw_hand(board["north"], (compass_w-nw_)//2, north_y)

    sh_ = parse_hand(board["south"])
    sw_ = max(tw(SUIT_SYMBOL[s]+" "+(sh_[s] or "-"), fhand) for s in SUIT_ORDER)
    draw_hand(board["south"], (compass_w-sw_)//2, south_y)

    wh  = parse_hand(board["west"])
    ww_ = max(tw(SUIT_SYMBOL[s]+" "+(wh[s] or "-"), fhand) for s in SUIT_ORDER)
    wx_ = max(PAD, bx - PAD - ww_)
    draw_hand(board["west"], wx_, side_y)
    draw_hand(board["east"], bx2+PAD, side_y)

    fbnum_bi = make_bold_italic_font(44)
    draw.text((PAD+8, PAD+40), str(board["board"]), fill=(0,0,0), font=fbnum_bi)

    hcp_n = calc_hcp(board["north"]); kr_n = calc_krhcp(board["north"])
    hcp_s = calc_hcp(board["south"]); kr_s = calc_krhcp(board["south"])
    hcp_e = calc_hcp(board["east"]);  kr_e = calc_krhcp(board["east"])
    hcp_w = calc_hcp(board["west"]);  kr_w = calc_krhcp(board["west"])
    hcol  = (50, 50, 50); krcol = (80, 80, 180)
    hcph  = th(fhcpb)

    DD_COL_SUITS   = ["C","D","H","S","NT"]
    DD_ROW_PLAYERS = ["N","S","E","W"]
    rh      = max(15, th(ftv)+6); hdr_h = rh
    lbl_cw  = max(18, tw("W",fth)+10)
    suit_cw = max(20, tw("13",ftv)+10)
    tbl_total_w = lbl_cw + 5*suit_cw
    tbl_total_h = hdr_h + len(DD_ROW_PLAYERS)*rh
    _ppm_dt = 1240/210; _dt_off = round(3.5*_ppm_dt/1.4142)
    dt_x = bx2 + _dt_off
    dt_y = by2 + _dt_off
    dt_x = max(PAD, min(dt_x, cell_w-PAD-tbl_total_w))
    dt_y = max(PAD, min(dt_y, cell_h-PAD-tbl_total_h))
    dt_x += round(5*1240/210) + round(3*1240/210)
    tbl_right = dt_x + tbl_total_w

    _ppm = 1240/210
    _hcp_w = round(10.0*_ppm); _hcp_h = round(7.5*_ppm)
    _hcp_rx2 = tbl_right; _hcp_ry1 = north_y - 4
    _hcp_rx1 = _hcp_rx2 - _hcp_w - round(10*_ppm)
    _hcp_ry2 = _hcp_ry1 + _hcp_h + 20
    _hcp_cx  = (_hcp_rx1+_hcp_rx2)//2; _hcp_cy = (_hcp_ry1+_hcp_ry2)//2

    def draw_hcp_kr(x, y, hcp_val, kr_val, anchor="c"):
        hstr = str(hcp_val); kstr = "({})" .format(kr_val)
        hw_ = tw(hstr, fhcpb); kw_ = tw(kstr, fkr); gap = 2
        total_w = hw_+gap+kw_
        sx = x-total_w//2 if anchor=="c" else (x-total_w if anchor=="r" else x)
        draw.text((sx, y), hstr, fill=hcol, font=fhcpb)
        draw.text((sx+hw_+gap, y+hcph//2-th(fkr)//2), kstr, fill=krcol, font=fkr)

    draw_hcp_kr(_hcp_cx, _hcp_ry1+1,        hcp_n, kr_n, "c")
    draw_hcp_kr(_hcp_cx, _hcp_ry2-hcph-1,   hcp_s, kr_s, "c")
    draw_hcp_kr(_hcp_rx1+2, _hcp_cy-hcph//2, hcp_w, kr_w, "l")
#    draw_hcp_kr(_hcp_rx2-2, _hcp_cy-hcph//2, hcp_e, kr_e, "r")
    draw_hcp_kr(_hcp_rx2 - 3, _hcp_cy-hcph//2, hcp_e, kr_e, "r")

    if dds_table:
        draw.rectangle([dt_x, dt_y, dt_x+tbl_total_w-1, dt_y+hdr_h-1], fill=(220,220,240))
        for ci, suit in enumerate(DD_COL_SUITS):
            cx_ = dt_x+lbl_cw+ci*suit_cw
            sym, sc = ("NT",(0,0,100)) if suit=="NT" else (SUIT_SYMBOL[suit], SUIT_COLOR[suit])
            draw.text((cx_+(suit_cw-tw(sym,fth))//2, dt_y+1), sym, fill=sc, font=fth)
        for ri, player in enumerate(DD_ROW_PLAYERS):
            ry  = dt_y+hdr_h+ri*rh
            rbg = (245,245,255) if ri%2==0 else (255,255,255)
            draw.rectangle([dt_x, ry, dt_x+tbl_total_w-1, ry+rh-1], fill=rbg)
            draw.text((dt_x+(lbl_cw-tw(player,fth))//2, ry+1), player, fill=(0,0,100), font=fth)
            for ci, suit in enumerate(DD_COL_SUITS):
                tricks = dds_table.get(player,{}).get(suit,0)
                level  = tricks - 6
                if level <= 0: continue
                vs = str(level); vx = dt_x+lbl_cw+ci*suit_cw
                if level==7: vc=(140,0,140)
                elif level==6: vc=(0,100,0)
                elif level>={"NT":3,"S":4,"H":4,"D":5,"C":5}.get(suit,4): vc=(0,0,180)
                else: vc=(0,0,0)
                draw.text((vx+(suit_cw-tw(vs,ftv))//2, ry+1), vs, fill=vc, font=ftv)
        draw.rectangle([dt_x, dt_y, dt_x+tbl_total_w-1, dt_y+tbl_total_h-1],
                       outline=(150,150,150), width=1)
        draw.line([(dt_x+lbl_cw, dt_y),(dt_x+lbl_cw, dt_y+tbl_total_h-1)],
                  fill=(150,150,150), width=1)
        draw.line([(dt_x, dt_y+hdr_h),(dt_x+tbl_total_w-1, dt_y+hdr_h)],
                  fill=(150,150,150), width=1)

    if caption:
        fcap   = make_bold_italic_font(max(7, fs_th+4))
        cap_lh = th(fcap)+1; RED=(200,0,0); BLACK=(40,40,40)
        lines  = caption.split("\n")
        max_w  = cell_w - 2*PAD
        for li, line in enumerate(lines):
            line_max_w = int(cell_w*0.80) if li==0 else max_w
            tmp_w = max(cell_w*3, sum(tw(ch,fcap) for ch in line)+4)
            line_h = th(fcap)
            tmp = Image.new("RGBA",(tmp_w, line_h+4),(255,255,255,0))
            tdraw = ImageDraw.Draw(tmp); x_=0
            for ch in line:
                color = RED if ch in ("♥","♦") else BLACK
                tdraw.text((x_,0), ch, fill=color+(255,), font=fcap); x_+=tw(ch,fcap)
            text_w = x_
            tmp = tmp.crop((0,0,text_w,line_h+4))
            if text_w > line_max_w:
                tmp = tmp.resize((line_max_w, line_h+4), Image.LANCZOS)
                paste_x = (cell_w-line_max_w)//2
            else:
                paste_x = max(PAD,(cell_w-text_w)//2)
            img.paste(tmp,(paste_x, 2+li*cap_lh), tmp)

    draw.rectangle([0,0,cell_w-1,cell_h-1], outline=(160,160,160), width=1)
    return img

def render_boards(boards, pair_results=None):
    cell_w = (A4_W - 2*MARGIN - 2*PADDING) // COLS
    cell_h = (A4_H - 2*MARGIN - 2*PADDING) // ROWS
    images = []
    for board in boards:
        img = render_board(board, cell_w, cell_h, pair_results=pair_results)
        images.append(img)
    return images, cell_w, cell_h

def assemble_pages_to_bytes(images, cell_w, cell_h, header="", pair_results=None, boards=None):
    n_pages = math.ceil(len(images) / BOARDS_PER_PAGE)
    pages   = []
    fhdr    = make_bold_font(28)

    for page_idx in range(n_pages):
        page = Image.new("RGB", (A4_W, A4_H), (255,255,255))
        draw = ImageDraw.Draw(page)
        if header:
            draw.text((MARGIN, 10), header, fill=(30,30,30), font=fhdr)
        slice_ = images[page_idx*BOARDS_PER_PAGE:(page_idx+1)*BOARDS_PER_PAGE]
        for i, img in enumerate(slice_):
            row = i // COLS; col = i % COLS
            x = MARGIN + col*(cell_w + PADDING)
            y = MARGIN + PADDING + row*(cell_h + PADDING)
            if header: y += 40
            page.paste(img, (x, y))
        pages.append(page)

    buf = io.BytesIO()
    if pages:
        pages[0].save(buf, format="PDF", save_all=True,
                      append_images=pages[1:], resolution=150)
    buf.seek(0)
    return buf.read()

# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Personalized EOM Hand Records",
    page_icon="clubs.png",
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

# Δημιουργούμε δύο στήλες: μια μικρή για το logo και μια μεγάλη για τον τίτλο
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] {
            align-items: center;
            display: flex;
        }
        /* Προαιρετικά: Μειώνει το κενό πάνω από τον τίτλο */
        .stApp h1 {
            padding-top: 0rem;
        }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 

    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")

    with col2:
        # Χρησιμοποιούμε h3 ή h2 αν το Title σου φαίνεται πολύ μεγάλο τώρα που μίκρυνε η εικόνα
        st.title("Personalized EOM Hand Records")

#    st.title(" Personalized EOM Hand Records")
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

# Δημιουργούμε δύο στήλες: μια μικρή για το logo και μια μεγάλη για τον τίτλο
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] {
            align-items: center;
            display: flex;
        }
        /* Προαιρετικά: Μειώνει το κενό πάνω από τον τίτλο */
        .stApp h1 {
            padding-top: 0rem;
        }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 

    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")

    with col2:
        # Χρησιμοποιούμε h3 ή h2 αν το Title σου φαίνεται πολύ μεγάλο τώρα που μίκρυνε η εικόνα
        st.title("Personalized EOM Hand Records")

#    st.title(" Personalized EOM Hand Records")
#    st.title(" Personalized EOM Hand Records")
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

# Δημιουργούμε δύο στήλες: μια μικρή για το logo και μια μεγάλη για τον τίτλο
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] {
            align-items: center;
            display: flex;
        }
        /* Προαιρετικά: Μειώνει το κενό πάνω από τον τίτλο */
        .stApp h1 {
            padding-top: 0rem;
        }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 

    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")

    with col2:
        # Χρησιμοποιούμε h3 ή h2 αν το Title σου φαίνεται πολύ μεγάλο τώρα που μίκρυνε η εικόνα
        st.title("Personalized EOM Hand Records")

#    st.title(" Personalized EOM Hand Records")
#    st.title(" Personalized EOM Hand Records")
    st.markdown("### Επιλογή Τουρνουά Τελευταίων Τριών Ημερών")

    if not st.session_state.tournaments:
        with st.spinner("Ανάκτηση δεδομένων..."):
            raw_t = scrape_tournament_list(max_page=8)
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
        st.session_state.step = "auth"
        st.rerun()

# ── STEP 4: Generate PDF ─────────────────────────────────────────────────────
elif st.session_state.step == "generate":

    # Initialization of error state
    if "error_msg" not in st.session_state:
        st.session_state.error_msg = None

    # UI Header logic (Logo + Title)
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] { align-items: center; display: flex; }
        .stApp h1 { padding-top: 0rem; }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 
    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")
    with col2:
        st.title("Personalized EOM Hand Records")

    # Αν υπάρχει σφάλμα, εμφάνισέ το και σταμάτησε την υπόλοιπη ροή
    if st.session_state.error_msg:
        st.error(st.session_state.error_msg)
        if st.button("⬅ Πίσω στα τουρνουά", type="primary"):
            st.session_state.error_msg = None
            st.session_state.chosen_url = None
            st.session_state.chosen_title = None
            st.session_state.step = "pick"
            st.rerun()
        st.stop()

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
            st.session_state.error_msg = "Δεν υπάρχουν διανομές για το τουρνουά που επιλέξατε."
            st.rerun()

        boards = parse_pbn(pbn_text)
        if not boards:
            st.session_state.error_msg = "Δεν βρέθηκαν boards στο PBN αρχείο."
            st.rerun()

        with st.spinner("Αναζήτηση αποτελεσμάτων παίκτη…"):
            header      = scrape_tournament_info(page_url)
            card_url    = find_card_url(page_url, reg)
            pair_results = {}
            if card_url:
                pair_results = scrape_pair_results(card_url, page_url)
            else:
                st.session_state.error_msg = "Ο αθλητής δεν συμμετείχε στο τουρνουά που επιλέξατε."
                st.rerun()

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
        st.session_state.error_msg = None
        st.session_state.step = "pick"
        st.rerun()
