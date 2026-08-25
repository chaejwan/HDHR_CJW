# HDHR 워크숍 — 설문조사 양식 페이지

항목(질문)과 답변(보기)을 자유롭게 추가해 설문지를 만들고, 바로 응답까지 해볼 수 있는
단일 페이지 웹 앱입니다. 빌드 도구나 서버 없이 정적 파일만으로 동작합니다.

## 실행 방법

**공개 주소**: https://chaejwan.github.io/HDHR_CJW/survey/

로컬에서 보려면 `survey/index.html` 을 브라우저로 열면 끝입니다. 로컬 서버로 띄우려면:

```bash
npx http-server -p 8080 .
# 목록 페이지: http://localhost:8080
# 설문 페이지: http://localhost:8080/survey/
```

## 기능

**편집 탭**

- 설문지 제목 / 설명 작성
- `＋ 항목 추가` 로 질문 추가, `＋ 답변 추가` 로 보기 추가
- 답변 유형 5가지: 단답형 · 장문형 · 객관식(하나 선택) · 체크박스(복수 선택) · 드롭다운
- 항목별 필수 응답 지정, 위/아래 순서 이동, 복제, 삭제
- 답변 삭제(✕) — 보기가 하나만 남으면 삭제 버튼이 숨겨집니다

**미리보기 탭**

- 실제 응답자가 보게 될 화면으로 렌더링
- 제출 시 필수 항목 검증 후 응답 결과를 정리해 표시
- `다시 응답하기` 로 초기화

**그 외**

- 작성 내용은 브라우저 `localStorage` 에 자동 저장되어 새로고침해도 유지됩니다
- `JSON 복사` 로 설문 구조를 JSON 으로 클립보드에 복사 (서버 연동 시 그대로 사용 가능)
- `초기화` 로 처음부터 다시 시작

## 파일 구성

```
index.html               실습물 목록 페이지 (사이트 첫 화면)
assets/home.css          목록 페이지 스타일

survey/index.html        설문 양식 마크업과 항목/답변 <template>
survey/assets/styles.css 설문 양식 스타일
survey/assets/app.js     상태 관리, 편집기·미리보기 렌더링, 응답 검증
```

### 실습물 추가하기

GitHub Pages 는 저장소당 사이트 하나(브랜치 하나)만 게시하므로, 실습물은 폴더로 나눕니다.

1. 저장소 루트에 폴더를 만들고 그 안에 `index.html` 을 둡니다. (예: `timesheet/index.html`)
2. 루트 `index.html` 의 `<a class="card">` 블록을 복사해 링크와 문구를 바꿉니다.
3. 푸시하면 `https://chaejwan.github.io/HDHR_CJW/폴더이름/` 으로 열립니다.

## 데이터 구조

`JSON 복사` 로 얻는 설문 데이터 형식입니다.

```json
{
  "title": "사내 만족도 조사",
  "description": "솔직하게 답변해 주세요.",
  "questions": [
    {
      "id": "a1b2c3d4",
      "label": "이 설문을 어떻게 알게 되셨나요?",
      "type": "radio",
      "required": true,
      "options": [
        { "id": "e5f6g7h8", "text": "사내 공지" },
        { "id": "i9j0k1l2", "text": "동료 추천" }
      ]
    }
  ]
}
```

`type` 은 `short` · `long` · `radio` · `checkbox` · `select` 중 하나이며,
`options` 는 선택형(`radio` · `checkbox` · `select`)에서만 사용됩니다.

## 응답 저장

현재는 응답을 화면에 보여주기만 합니다. 서버에 저장하려면 `survey/assets/app.js` 의
`el.form.addEventListener('submit', ...)` 안에서 수집된 `answers` 배열을
백엔드로 전송하도록 확장하면 됩니다.
