# 경계 데이터 준비

이 폴더의 `build_data.py` 는 원천 경계 데이터를 뷰어가 읽는 형태로 바꾼다.
결과물은 `district/data/` 에 들어가며 저장소에 그대로 커밋한다.

```
원천(GeoJSON/SHP/API)  →  build_data.py  →  data/*.geojson + data/manifest.json
```

빌드 단계에서 하는 일:

- 대상 시군구(성남시 분당구)만 골라내기
- 구역별 면적(구면 기준 km²) 계산
- 라벨 위치 계산 — 중심점(centroid)은 U자형 구역에서 폴리곤 밖으로 나가므로
  내접원 중심(pole of inaccessibility)을 쓴다
- 인접 구역끼리 같은 색이 되지 않도록 그리디 그래프 컬러링
- 구 전체 외곽선 생성(dissolve)
- 좌표 소수점 6자리(약 11cm)로 반올림해 파일 크기 축소

필요한 것은 파이썬 3.9+ 와 `shapely` 뿐이다.

```bash
pip install shapely
```

---

## 1. 행정동 — 자동

행정동은 공개 저장소에서 바로 받을 수 있어 명령 한 줄이면 끝난다.

```bash
cd district
python3 tools/build_data.py \
  --hjd-url https://raw.githubusercontent.com/vuski/admdongkor/master/ver20260701/HangJeongDong_ver20260701.geojson \
  --hjd-source "통계청 SGIS 기반 admdongkor ver20260701 (CC BY 4.0)"
```

행정동은 분동·통합이 잦으므로 `ver...` 부분을 최신 시점으로 바꿔 주기적으로 다시 돌리면 된다.
시점 목록은 [vuski/admdongkor](https://github.com/vuski/admdongkor) 에서 확인한다.

---

## 2. 법정동 — 원천을 한 번 준비해야 한다

**행정동을 합쳐서 법정동을 만들 수는 없다.** 두 체계는 N:M 관계다. 하나의 행정동이 여러
법정동을 관할하기도 하고, 하나의 법정동이 여러 행정동에 걸치기도 한다. 수내동처럼
법정동 하나가 행정동 수내1·2·3동으로 깔끔하게 나뉘는 경우만 보면 합집합으로 될 것 같지만,
행정동 하나가 여러 법정동을 아우르는 구역에서는 그 안쪽 법정동 경계선이 행정동 데이터에
아예 존재하지 않는다. 그래서 법정동은 별도 원천이 필요하다.

아래 두 방법 중 편한 쪽을 쓴다.

### 방법 A — VWorld 데이터 API (권장)

1. <https://www.vworld.kr> 가입 후 [오픈API 인증키]를 발급받는다(무료).
   활용 URL에는 `https://chaejwan.github.io` 를 넣어 두면 된다.
2. 키를 넣어 실행한다.

```bash
cd district
python3 tools/build_data.py \
  --hjd-url https://raw.githubusercontent.com/vuski/admdongkor/master/ver20260701/HangJeongDong_ver20260701.geojson \
  --hjd-source "통계청 SGIS 기반 admdongkor ver20260701 (CC BY 4.0)" \
  --bjd-vworld-key <발급받은_키>
```

대상 구역의 사각 범위로 넉넉히 받아온 뒤 법정동코드 앞 5자리(`41135`)로 다시 거르므로,
배포판마다 속성 이름이 달라도 잘 동작한다.

> 이 경로의 네트워크 호출은 개발 환경에서 VWorld 접속이 막혀 있어 실제 응답으로 검증하지
> 못했다. 응답 형식이 달라 실패하면 아래 방법 B 를 쓰거나, 받은 JSON 을 파일로 저장한 뒤
> `--bjd-file` 로 넘기면 된다.

### 방법 B — 주소기반산업지원서비스 SHP (정밀도 최상)

네이버지도·카카오맵이 쓰는 것과 같은 계열의 원천이고, 월 1회 갱신되어 가장 정확하다.
대신 회원가입과 수동 다운로드가 필요하다.

1. <https://business.juso.go.kr/jst/jstAddressDetailsSearch> 에서
   **구역도형(법정동)** 자료를 내려받는다.
2. SHP 를 GeoJSON(EPSG:4326)으로 바꾼다. 둘 중 하나면 된다.

   ```bash
   # mapshaper (npm install -g mapshaper)
   mapshaper TL_SCCO_EMD.shp -proj wgs84 -o format=geojson bjd.geojson

   # 또는 GDAL
   ogr2ogr -t_srs EPSG:4326 -f GeoJSON bjd.geojson TL_SCCO_EMD.shp
   ```

   원본 SHP 의 좌표계는 보통 EPSG:5179(UTM-K) 또는 EPSG:5186 이다. `.prj` 를 확인하지 않고
   4326 으로 가정하면 지도가 엉뚱한 곳에 그려지므로 변환을 반드시 명시한다.

3. 변환한 파일을 넘긴다.

   ```bash
   python3 tools/build_data.py \
     --hjd-url <위와 동일> \
     --bjd-file bjd.geojson \
     --bjd-source "행정안전부 주소기반산업지원서비스 법정구역 (2026-07 기준)"
   ```

`build_data.py` 는 `EMD_CD` / `emd_cd` / `ademd_cd`, `EMD_KOR_NM` / `emd_kor_nm` 등
흔한 속성 이름을 자동으로 인식한다. 인식하지 못하면 "대상 시군구에 해당하는 법정동을 찾지
못했습니다" 라고 알려 주므로, 그때 `normalise_bjd()` 의 후보 목록에 실제 필드명을 추가하면 된다.

---

## 다른 지역으로 넓히기

`build_data.py` 상단의 `TARGET` 을 바꾸면 된다.

```python
TARGET = {
    "slug": "yongin",
    "sido": "경기도",
    "sgg": "용인시 수지구",
    "sgg_match": "용인시수지구",   # admdongkor 의 sggnm 은 공백이 없다
    "sgg_code": "41465",
    "bbox": [127.03, 37.28, 127.15, 37.36],
}
```

전국으로 넓힐 때는 GeoJSON 을 그대로 두면 용량이 문제가 된다(법정동은 전국 약 2만 폴리곤).
그 단계에서는 tippecanoe 로 PMTiles 를 만들어 벡터 타일로 바꾸는 편이 낫다.
GitHub Pages 는 파일당 100MB 제한이 있으니 그 전에 외부 스토리지도 함께 검토한다.

---

## 정확도에 대해

지금 들어 있는 행정동 경계는 통계청 SGIS 계열로, 일반화(단순화)가 어느 정도 적용된
자료다. 22개 행정동의 면적 합계는 69.89 km² 로 계산되며, 성남시가 공표하는 분당구 면적과
대조해 원천이 제대로 들어왔는지 확인할 수 있다. 다만 경계선의 잔 굴곡은
네이버지도·카카오맵보다 완만하다.

그 수준까지 올리려면 위 **방법 B** 의 주소기반산업지원서비스 원천을 행정동에도 쓰면 된다.
같은 곳에서 행정동 구역도형도 함께 제공한다.
