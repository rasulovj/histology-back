"""
KTP (Календарно-тематический план) service.
Parses the .docx KTP file and provides topic lookup by faculty.
"""

import os
import json
from docx import Document

KTP_JSON = os.path.join("data", "ktp.json")

# Map every known faculty name (all languages) → canonical key
FACULTY_MAP = {
    # Russian
    "Лечебное дело":                              "fac_lech",
    "Фундаментальная медицина":                   "fac_biomed",
    "Медико-профилактическое дело":               "fac_medprof",
    "Стоматология":                               "fac_stom",
    "Педиатрия":                                  "fac_ped",
    "Мед. педагогика":                            "fac_medped",
    "Фармация":                                   "fac_farm",
    "ВМСО (Медсестры)":                           "fac_nurse",
    "Воен. медицина":                             "fac_mil",
    "Международный":                              "fac_inter",
    "Ординатура/Магистратура":                    "fac_postgrad",
    "Лечебное дело    (Международный факультет)": "fac_inter",
    "Стоматология     (М/Ф)":                    "fac_stom_inter",
    # English
    "General Medicine":                           "fac_lech",
    "Biomedical":                                 "fac_biomed",
    "Medical Prevention":                         "fac_medprof",
    "Dentistry":                                  "fac_stom",
    "Pediatrics":                                 "fac_ped",
    "Medical Pedagogy":                           "fac_medped",
    "Pharmacy":                                   "fac_farm",
    "Higher Nursing":                             "fac_nurse",
    "Military Medicine":                          "fac_mil",
    "International":                              "fac_inter",
    "Residency/Masters":                          "fac_postgrad",
    # Uzbek
    "Davolash ishi":                              "fac_lech",
    "Biomeditsina":                               "fac_biomed",
    "Tibbiy profilaktika":                        "fac_medprof",
    "Stomatologiya":                              "fac_stom",
    "Pediatriya":                                 "fac_ped",
    "Tibbiy pedagogika":                          "fac_medped",
    "Farmatsiya":                                 "fac_farm",
    "OMH (Hamshiralik)":                          "fac_nurse",
    "Harbiy tibbiyot":                            "fac_mil",
    "Xalqaro fakultet":                           "fac_inter",
    "Ordinatura/Magistratura":                    "fac_postgrad",
}

# Reverse map: bot faculty key → KTP faculty name
FACULTY_KEY_TO_NAME = {v: k for k, v in FACULTY_MAP.items()}


def parse_ktp(docx_path: str) -> dict:
    """Parse KTP docx → {faculty_key: {lectures: [...], practicals: [...]}}"""
    from docx.oxml.ns import qn

    doc = Document(docx_path)
    result = {}

    faculty = None
    table_idx = -1
    is_lecture = False

    for child in doc.element.body:
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            text = ''.join(n.text or '' for n in child.iter(qn('w:t'))).strip()
            if text.startswith('Факультет:'):
                faculty = text.replace('Факультет:', '').strip()
            elif 'лекцион' in text.lower():
                is_lecture = True
            elif 'практическ' in text.lower():
                is_lecture = False
        elif tag == 'tbl':
            table_idx += 1
            if not faculty:
                continue
            fkey = FACULTY_MAP.get(faculty, faculty)
            if fkey not in result:
                result[fkey] = {"lectures": [], "practicals": []}

            table = doc.tables[table_idx]
            topics = []
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                # Skip header row and totals row
                if not cells[0].isdigit():
                    continue
                num, date, topic, hours = (cells + ['', '', '', ''])[:4]
                if topic:
                    topics.append({
                        "num": int(num),
                        "date": date,
                        "topic": topic,
                        "hours": hours,
                    })

            if is_lecture:
                result[fkey]["lectures"] = topics
            else:
                result[fkey]["practicals"] = topics

    return result


def load_ktp() -> dict:
    """Load parsed KTP from JSON cache."""
    if os.path.exists(KTP_JSON):
        with open(KTP_JSON, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_ktp(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(KTP_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_topics_for_faculty(faculty_key: str, kind: str = "practicals") -> list:
    """
    Return list of topic dicts for a faculty.
    kind: 'lectures' or 'practicals'
    faculty_key: bot faculty key like 'fac_lech', or raw faculty name from profile.
    """
    ktp = load_ktp()
    if not ktp:
        return []

    # Try direct key first
    entry = ktp.get(faculty_key)

    # If not found, try matching by raw faculty name
    if not entry:
        mapped = FACULTY_MAP.get(faculty_key)
        if mapped:
            entry = ktp.get(mapped)

    # Fallback: try partial match on KTP keys
    if not entry:
        for key in ktp:
            if faculty_key.lower() in key.lower() or key.lower() in faculty_key.lower():
                entry = ktp[key]
                break

    if not entry:
        return []

    return entry.get(kind, [])


def get_topic_label(topic: dict, lang: str) -> str:
    """Return the topic name in the requested language, falling back to Russian."""
    if lang == "en" and topic.get("topic_en"):
        return topic["topic_en"]
    if lang == "uz" and topic.get("topic_uz"):
        return topic["topic_uz"]
    return topic["topic"]


def get_all_faculties() -> list:
    """Return list of faculty keys that have KTP data."""
    ktp = load_ktp()
    return list(ktp.keys())
