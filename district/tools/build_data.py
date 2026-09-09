#!/usr/bin/env python3
"""행정구역 경계 데이터 빌드 스크립트.

원천 경계 데이터(행정동 / 법정동)를 받아 뷰어가 쓰는 GeoJSON + 메타데이터로 변환한다.

기본 사용(행정동, 자동 다운로드):
    python3 tools/build_data.py --hjd-url <admdongkor geojson URL>
    python3 tools/build_data.py --hjd-file /path/to/HangJeongDong_verYYYYMMDD.geojson

법정동(고정밀, 수동 1회 준비 필요 — tools/README.md 참고):
    python3 tools/build_data.py --bjd-file /path/to/beopjeongdong.geojson

산출물:
    data/<slug>.hjd.geojson      행정동 경계
    data/<slug>.bjd.geojson      법정동 경계 (원천이 있을 때만)
    data/<slug>.outline.geojson  대상 구역 전체 외곽선
    data/manifest.json           뷰어가 읽는 목록/메타
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

from shapely.algorithms.polylabel import polylabel
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

EARTH_RADIUS_M = 6371008.8

# 대상 구역. 다른 시군구로 넓힐 때 여기에 추가한다.
TARGET = {
    "slug": "bundang",
    "sido": "경기도",
    "sgg": "성남시 분당구",
    # admdongkor 의 sggnm 은 '성남시분당구' 처럼 공백이 없다.
    "sgg_match": "성남시분당구",
    "sgg_code": "41135",
    # VWorld 조회용 대략 범위 (여유를 둔 뒤 코드로 다시 거른다).
    "bbox": [127.00, 37.31, 127.20, 37.44],
}


# ---------------------------------------------------------------- 기하 유틸


def spherical_area_km2(geom) -> float:
    """구면 위 폴리곤 면적(km²). 이 축척에서 오차는 0.1% 미만이다."""

    def ring_area(coords) -> float:
        total = 0.0
        for i in range(len(coords) - 1):
            lon1, lat1 = math.radians(coords[i][0]), math.radians(coords[i][1])
            lon2, lat2 = math.radians(coords[i + 1][0]), math.radians(coords[i + 1][1])
            total += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
        return abs(total * EARTH_RADIUS_M * EARTH_RADIUS_M / 2.0) / 1_000_000

    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    area = 0.0
    for poly in polys:
        area += ring_area(list(poly.exterior.coords))
        for interior in poly.interiors:
            area -= ring_area(list(interior.coords))
    return area


def label_point(geom) -> list[float]:
    """라벨을 찍을 지점. centroid 는 U자형 구역에서 폴리곤 밖으로 나가므로
    pole of inaccessibility(내접원 중심)를 쓴다."""
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    largest = max(polys, key=lambda p: p.area)
    try:
        pt = polylabel(largest, tolerance=1e-5)
    except Exception:
        pt = largest.representative_point()
    return [round(pt.x, 7), round(pt.y, 7)]


def round_geojson(obj, ndigits: int = 6):
    """좌표 소수점 자리수 축소. 6자리는 약 11cm 해상도로, 경계 표시에는 충분하고
    파일 크기는 눈에 띄게 줄어든다."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, list):
        return [round_geojson(x, ndigits) for x in obj]
    if isinstance(obj, dict):
        return {k: round_geojson(v, ndigits) for k, v in obj.items()}
    return obj


def build_adjacency(features: list[dict]) -> dict[str, list[str]]:
    """인접 구역 목록. 색을 칠할 때 맞닿은 구역이 같은 색이 되지 않게 하는 데 쓴다."""
    geoms = {f["properties"]["code"]: shape(f["geometry"]) for f in features}
    codes = list(geoms)
    adjacency: dict[str, list[str]] = {c: [] for c in codes}
    for i, a in enumerate(codes):
        for b in codes[i + 1 :]:
            # 좌표 반올림으로 접점이 미세하게 벌어질 수 있어 여유를 둔다.
            if geoms[a].distance(geoms[b]) < 1e-6:
                adjacency[a].append(b)
                adjacency[b].append(a)
    return adjacency


def assign_colors(features: list[dict], adjacency: dict[str, list[str]], palette_size: int) -> dict[str, int]:
    """인접 구역끼리 다른 색이 되도록 그리디 그래프 컬러링."""
    order = sorted(adjacency, key=lambda c: -len(adjacency[c]))
    colors: dict[str, int] = {}
    for code in order:
        used = {colors[n] for n in adjacency[code] if n in colors}
        for idx in range(palette_size):
            if idx not in used:
                colors[code] = idx
                break
        else:
            colors[code] = len(colors) % palette_size
    return colors


# ---------------------------------------------------------------- 원천 로딩


def load_geojson(path_or_url: str) -> dict:
    if path_or_url.startswith(("http://", "https://")):
        print(f"  내려받는 중: {path_or_url}")
        with urllib.request.urlopen(path_or_url, timeout=300) as res:
            return json.loads(res.read().decode("utf-8"))
    return json.loads(Path(path_or_url).read_text(encoding="utf-8"))


def pick(props: dict, *keys: str) -> str | None:
    """원천마다 속성 이름이 달라서(EMD_KOR_NM / adm_nm / EMD_CD …) 후보를 순서대로 본다."""
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def normalise_hjd(raw: dict) -> list[dict]:
    """admdongkor(통계청 SGIS 기반) 행정동 GeoJSON → 표준 형태."""
    out = []
    for feat in raw["features"]:
        props = feat["properties"]
        if TARGET["sgg_match"] not in (props.get("sggnm") or ""):
            continue
        full = props.get("adm_nm") or ""
        out.append(
            {
                "type": "Feature",
                "properties": {
                    "code": pick(props, "adm_cd2", "adm_cd"),
                    "name": full.split()[-1] if full else "",
                    "full_name": full,
                    "level": "hjd",
                },
                "geometry": feat["geometry"],
            }
        )
    return out


def normalise_bjd(raw: dict) -> list[dict]:
    """법정동 원천 GeoJSON → 표준 형태.

    주소기반산업지원서비스 SHP(TL_SCCO_EMD 계열)를 mapshaper/ogr2ogr 로 GeoJSON 변환한
    결과를 가정한다. 속성 이름이 배포판마다 달라 후보를 넓게 잡는다."""
    out = []
    for feat in raw["features"]:
        props = feat["properties"]
        code = pick(props, "EMD_CD", "emd_cd", "ademd_cd", "ADM_CD", "code", "bjd_cd")
        name = pick(props, "EMD_KOR_NM", "emd_kor_nm", "ademd_kor_nm", "EMD_NM", "name", "bjd_nm")
        if not code:
            continue
        # 법정동코드 10자리 중 앞 5자리가 시군구코드.
        if not str(code).startswith(TARGET["sgg_code"]):
            continue
        out.append(
            {
                "type": "Feature",
                "properties": {
                    "code": str(code),
                    "name": name or "",
                    "full_name": f"{TARGET['sido']} {TARGET['sgg']} {name or ''}".strip(),
                    "level": "bjd",
                },
                "geometry": feat["geometry"],
            }
        )
    return out


def fetch_vworld_bjd(key: str, layer: str = "LT_C_ADEMD_INFO", page_size: int = 1000) -> dict:
    """VWorld 데이터 API 에서 법정동(읍면동) 경계를 GeoJSON 으로 받는다.

    속성 이름을 추측해 attrFilter 를 거는 대신 대상 구역의 사각 범위로 넉넉히 받아온 뒤
    법정동코드 앞 5자리로 다시 거른다. 배포판마다 필드 이름이 달라도 깨지지 않는다.

    VWorld 키는 https://www.vworld.kr 에서 무료로 발급받는다."""
    import urllib.parse

    bbox = TARGET["bbox"]
    features: list[dict] = []
    page = 1
    while True:
        params = {
            "service": "data",
            "request": "GetFeature",
            "data": layer,
            "key": key,
            "format": "json",
            "crs": "EPSG:4326",
            "geometry": "true",
            "attribute": "true",
            "size": str(page_size),
            "page": str(page),
            "geomFilter": "BOX({},{},{},{})".format(*bbox),
        }
        url = "https://api.vworld.kr/req/data?" + urllib.parse.urlencode(params)
        print(f"  VWorld 조회 page={page}")
        with urllib.request.urlopen(url, timeout=120) as res:
            payload = json.loads(res.read().decode("utf-8"))

        response = payload.get("response", {})
        status = response.get("status")
        if status != "OK":
            raise SystemExit(
                "VWorld 응답이 정상이 아닙니다: "
                + json.dumps(response.get("error", response), ensure_ascii=False)
            )

        collection = response.get("result", {}).get("featureCollection", {})
        batch = collection.get("features", [])
        features.extend(batch)
        total = int(response.get("record", {}).get("total", len(features)))
        if len(features) >= total or not batch:
            break
        page += 1

    print(f"  받은 피처: {len(features)}개")
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------- 레이어 빌드


def build_layer(features: list[dict], level: str, palette_size: int) -> tuple[dict, list[dict]]:
    if not features:
        return {"type": "FeatureCollection", "features": []}, []

    features.sort(key=lambda f: f["properties"]["code"])
    adjacency = build_adjacency(features)
    colors = assign_colors(features, adjacency, palette_size)

    index = []
    for feat in features:
        geom = shape(feat["geometry"])
        code = feat["properties"]["code"]
        feat["properties"]["color"] = colors[code]
        feat["properties"]["area_km2"] = round(spherical_area_km2(geom), 3)
        point = label_point(geom)
        feat["properties"]["label_lon"] = point[0]
        feat["properties"]["label_lat"] = point[1]
        bounds = geom.bounds
        index.append(
            {
                "code": code,
                "name": feat["properties"]["name"],
                "full_name": feat["properties"]["full_name"],
                "level": level,
                "color": colors[code],
                "area_km2": feat["properties"]["area_km2"],
                "label": point,
                "bbox": [round(v, 6) for v in bounds],
            }
        )

    return {"type": "FeatureCollection", "features": features}, index


def build_outline(feature_groups: list[list[dict]]) -> dict:
    geoms = [shape(f["geometry"]) for group in feature_groups for f in group]
    if not geoms:
        return {"type": "FeatureCollection", "features": []}
    # buffer(0) 로 미세한 자기교차를 정리한 뒤 합친다.
    merged = unary_union([g.buffer(0) for g in geoms])
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "code": TARGET["sgg_code"],
                    "name": TARGET["sgg"],
                    "full_name": f"{TARGET['sido']} {TARGET['sgg']}",
                    "level": "sgg",
                    "area_km2": round(spherical_area_km2(merged), 3),
                },
                "geometry": mapping(merged),
            }
        ],
    }


def write_json(path: Path, obj, ndigits: int = 6) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(round_geojson(obj, ndigits), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  썼음: {path.relative_to(ROOT)}  ({path.stat().st_size / 1024:.1f} KB)")


def main() -> int:
    parser = argparse.ArgumentParser(description="행정구역 경계 데이터 빌드")
    parser.add_argument("--hjd-file", help="행정동 경계 GeoJSON 경로")
    parser.add_argument("--hjd-url", help="행정동 경계 GeoJSON URL")
    parser.add_argument("--hjd-source", default="", help="행정동 출처 표기 문자열")
    parser.add_argument("--bjd-file", help="법정동 경계 GeoJSON 경로")
    parser.add_argument("--bjd-vworld-key", help="VWorld 인증키. 주면 법정동 경계를 API 로 직접 받는다.")
    parser.add_argument("--bjd-source", default="", help="법정동 출처 표기 문자열")
    parser.add_argument("--palette-size", type=int, default=8)
    parser.add_argument("--precision", type=int, default=6)
    args = parser.parse_args()

    if not args.hjd_file and not args.hjd_url and not args.bjd_file and not args.bjd_vworld_key:
        parser.error("--hjd-file / --hjd-url / --bjd-file / --bjd-vworld-key 중 최소 하나가 필요합니다.")

    slug = TARGET["slug"]
    layers = {}
    groups: list[list[dict]] = []

    if args.hjd_file or args.hjd_url:
        print("행정동 처리")
        raw = load_geojson(args.hjd_file or args.hjd_url)
        feats = normalise_hjd(raw)
        print(f"  대상 구역에서 {len(feats)}개 추출")
        fc, index = build_layer(feats, "hjd", args.palette_size)
        write_json(DATA / f"{slug}.hjd.geojson", fc, args.precision)
        layers["hjd"] = {
            "label": "행정동",
            "file": f"{slug}.hjd.geojson",
            "source": args.hjd_source,
            "count": len(index),
            "items": index,
        }
        groups.append(feats)

    if args.bjd_file or args.bjd_vworld_key:
        print("법정동 처리")
        if args.bjd_vworld_key:
            raw = fetch_vworld_bjd(args.bjd_vworld_key)
            if not args.bjd_source:
                args.bjd_source = "국토교통부 법정구역 (VWorld 데이터 API)"
        else:
            raw = load_geojson(args.bjd_file)
        feats = normalise_bjd(raw)
        print(f"  대상 구역에서 {len(feats)}개 추출")
        if feats:
            fc, index = build_layer(feats, "bjd", args.palette_size)
            write_json(DATA / f"{slug}.bjd.geojson", fc, args.precision)
            layers["bjd"] = {
                "label": "법정동",
                "file": f"{slug}.bjd.geojson",
                "source": args.bjd_source,
                "count": len(index),
                "items": index,
            }
            groups.append(feats)
        else:
            print("  경고: 대상 시군구에 해당하는 법정동을 찾지 못했습니다. 속성 이름을 확인하세요.")

    print("전체 외곽선 처리")
    # 외곽선은 한 체계만 써서 만든다. 두 체계를 섞으면 미세한 좌표 차이로 실밥이 생긴다.
    write_json(DATA / f"{slug}.outline.geojson", build_outline(groups[:1]), args.precision)

    manifest = {
        "slug": slug,
        "sido": TARGET["sido"],
        "sgg": TARGET["sgg"],
        "sgg_code": TARGET["sgg_code"],
        "outline": f"{slug}.outline.geojson",
        "palette_size": args.palette_size,
        "layers": layers,
    }
    write_json(DATA / "manifest.json", manifest, args.precision)
    print("완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
