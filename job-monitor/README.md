# 채용공고 모니터

지정한 채용 사이트를 **GitHub Actions 가 주기적으로 확인**해서, 새로 올라온 공고가 있으면
**이메일로 알려 주는** 도구입니다. 내 컴퓨터를 켜 둘 필요가 없습니다.

- **설정 화면(공개 주소)**: <https://chaejwan.github.io/HDHR_CJW/monitor/>
- **확인 주체**: `.github/workflows/job-monitor.yml` (GitHub Actions, 매시간 실행)
- **설정 파일**: `job-monitor/config.json` (설정 화면에서 저장하면 이 파일이 커밋됩니다)
- **확인 기록**: `job-monitor/data/state.json`, 마지막 실행 요약 `job-monitor/data/last-run.json`

```
설정 화면(GitHub Pages) ──저장──▶ config.json ──읽음──▶ Actions 워크플로 ──메일──▶ 내 메일함
        ▲                                                      │
        └──────────── state.json / last-run.json ◀─────────────┘ (확인 기록 커밋)
```

---

## 1. 처음 한 번만 하는 설정

### ① 기본 브랜치에 반영

GitHub 의 예약 실행(schedule)은 **기본 브랜치에 있는 워크플로만** 동작합니다.
이 저장소의 기본 브랜치는 `260825` 이므로, 작업 브랜치
`claude/job-posting-monitor-gyksu3` 를 `260825` 에 병합해야 실제로 돌기 시작합니다.
(설정 화면과 확인 기록도 기본 브랜치의 파일을 사용합니다.)

### ② Actions 에 쓰기 권한 주기

저장소 **Settings → Actions → General → Workflow permissions** 에서
**Read and write permissions** 를 선택하고 저장합니다.
(확인 기록 `state.json` 을 저장소에 커밋하기 위해 필요합니다. 이 값이 없으면 매번 처음부터
다시 확인하게 되어 같은 공고를 반복해서 알림 받습니다.)

### ③ 메일 계정 시크릿 등록

저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 등록합니다.
비밀번호는 절대 `config.json` 에 넣지 마세요. (워크플로가 첫 단계에서 검사해 실패시킵니다.)

| 시크릿 이름 | 값 | 필수 |
| --- | --- | --- |
| `JOBMON_SMTP_HOST` | `smtp.gmail.com` | ✔ |
| `JOBMON_SMTP_USER` | 보내는 계정 주소 | ✔ |
| `JOBMON_SMTP_PASSWORD` | Gmail **앱 비밀번호** | ✔ |
| `JOBMON_SMTP_FROM` | 보내는 사람 주소 (보통 위와 같음) | |
| `JOBMON_SMTP_PORT` / `JOBMON_SMTP_SECURITY` | `587` / `starttls` (기본값) | |
| `JOBMON_RECIPIENTS` | 받는 주소들 (`a@x.com, b@y.com`) | |

- 앱 비밀번호: Google 계정 → 보안 → 2단계 인증을 켠 뒤 **앱 비밀번호** 에서 발급.
- 네이버는 `smtp.naver.com` / `465` / `ssl`, 다음은 `smtp.daum.net` / `465` / `ssl`.
- 저장소가 **공개(public)** 라면 `config.json` 에 적은 수신 이메일도 공개됩니다.
  주소를 감추려면 설정 화면의 수신 이메일 칸을 비우고 `JOBMON_RECIPIENTS` 시크릿을 쓰세요.
  (시크릿이 설정보다 우선합니다.)

### ④ 설정 화면용 토큰 만들기

설정 화면은 GitHub Pages 에 올라간 정적 페이지라, 저장할 때 **본인 토큰**이 필요합니다.
토큰은 그 브라우저에만 저장되고 GitHub API 호출에만 쓰입니다.

1. GitHub → Settings → Developer settings → **Personal access tokens → Fine-grained tokens**
2. Repository access: **이 저장소만** 선택
3. Repository permissions: `Contents: Read and write`, `Actions: Read and write`
4. 발급된 토큰을 설정 화면의 **저장 권한** 칸에 붙여 넣고 `토큰 저장`

> 토큰을 쓰고 싶지 않다면, 설정 화면의 `설정 JSON 복사` 를 눌러
> 저장소의 `job-monitor/config.json` 을 GitHub 화면에서 직접 수정해도 똑같이 동작합니다.

---

## 2. 평소 사용법

<https://chaejwan.github.io/HDHR_CJW/monitor/> 에 접속해서

- **확인할 사이트**: 추가 · 이름/주소 수정 · 사용 여부 토글 · 삭제, 사이트별 주기와 필터
- **확인 주기**: 기본 24시간 (1시간 이상)
- **알림 이메일**: 수신 주소, 보내는 계정(SMTP) 정보
- **설정 저장**: `config.json` 을 커밋합니다. 저장하면 곧바로 확인이 한 번 실행됩니다.
- **지금 확인 실행**: 주기와 상관없이 모든 사이트를 즉시 확인합니다.
- 메일 설정이 맞는지 확인하려면 Actions 탭 → **채용공고 확인** → Run workflow 에서
  `테스트 메일만 보내기` 를 켜고 실행하세요. 받은 편지함에 테스트 메일이 오면 정상입니다.
- 화면 위쪽에서 **마지막 확인 결과**, 아래쪽에서 **최근 발견한 공고** 를 볼 수 있습니다.

> 설정 화면은 저장소 시크릿을 읽을 수 없습니다(보안상 GitHub 이 공개하지 않습니다).
> 그래서 메일 설정이 제대로 됐는지는 **실제 발송 결과**(`data/email-status.json`)로 판단합니다.
> 테스트 메일이 한 번 성공하면 화면 위 경고가 사라지고 `메일 설정 확인됨` 으로 바뀝니다.

사이트를 처음 등록하면 그때의 목록을 **기준**으로 저장만 하고 메일을 보내지 않습니다.
(이미 올라와 있던 공고 수십 건이 한꺼번에 오는 것을 막기 위해서입니다.)
그 다음 확인부터 새로 올라온 글만 알려 줍니다.

### 확인 주기에 대해

워크플로는 **30분마다**(UTC 기준 매시 7분·37분) 깨어나서, 각 사이트의 주기가 지난 것만
실제로 확인합니다. 따라서 주기는 30분까지 줄일 수 있습니다.

GitHub 의 예약 실행은 **보장되지 않습니다.** 혼잡할 때는 수십 분 늦거나 아예 건너뜁니다
(그래서 정각을 피해 7분·37분에 실행합니다). 즉 "30분마다"로 정해도 실제로는 1~2시간에
한 번이 될 수 있습니다. 급한 확인은 설정 화면의 **지금 확인 실행** 을 쓰세요.

> **작업 브랜치에서는 실행되지 않습니다.** 확인 기록(state.json)이 브랜치마다 다르기 때문에,
> 오래된 기록이 든 브랜치에서 돌리면 이미 알린 공고를 새 공고로 착각해 중복 메일을 보냅니다.
> 그래서 워크플로는 기본 브랜치에서만 동작하도록 막아 두었습니다.

> 저장소에 60일 동안 활동이 없으면 GitHub 가 예약 실행을 자동으로 멈춥니다.
> 이 워크플로는 실행할 때마다 확인 기록을 커밋하므로 보통 유지되지만, 멈췄다면
> **Actions 탭 → 워크플로 → Enable** 로 다시 켜면 됩니다. 공개 저장소는 Actions 사용료가 없습니다.

---

## 3. 사이트 목록이 잡히지 않을 때

설정 화면의 사이트 카드에 **추출 방식**이 표시됩니다.

| 추출 방식 | 의미 |
| --- | --- |
| `jsonld` | 페이지의 채용공고 구조화 데이터(JobPosting)에서 가져옴 — 가장 정확 |
| `embedded-json` | 페이지에 박혀 있는 JSON(`__NEXT_DATA__` 등)에서 가져옴 |
| `links` | 페이지의 링크 목록에서 가져옴 |
| `json` | 주소가 JSON API 인 경우 |
| `fingerprint` | 목록을 못 찾아서 **본문이 바뀌었는지만** 감시 |

**방법 1 — 필터로 다듬기.** `links` 로 잡히면 메뉴·푸터 링크까지 섞입니다.
세부 설정에서 주소 필터(`recruit|notice`), 제외 필터(`로그인|약관|개인정보`)를 지정하세요.

**방법 2 — 브라우저 렌더링.** 접속 후 자바스크립트로 목록을 그리는 사이트(한화 채용 페이지 등)는
원본 HTML 에 공고가 없습니다. 확인 방식을 **브라우저 렌더링** 으로 바꾸면 워크플로가 그 사이트에
한해 Chromium 을 설치해 실제로 화면을 그린 뒤 목록을 읽습니다(실행 시간이 1~2분 늘어납니다).

**방법 3 — JSON API 직접 지정 (가장 안정적).** 목록을 가져오는 API 주소를 사이트 주소로 넣고
확인 방식을 **JSON API** 로 바꾼 뒤 필드를 지정합니다.

| 칸 | 예시 | 설명 |
| --- | --- | --- |
| JSON 목록 경로 | `data.list` | 공고 배열이 있는 위치 |
| 제목 필드 | `rtNm` | 공고 제목 |
| 식별자 필드 | `rtSeq` | 같은 공고인지 구분할 값 |
| 상세 주소 형식 | `https://www.example.com/jobs/detail?seq={rtSeq}` | `{필드명}` 자리에 값이 들어갑니다 |
| 요청 방식 / 본문 | `POST` / `{"page":0,"size":50}` | 검색형 API 는 POST 로 조건을 보냅니다 |

비워 두면 흔한 이름(`title`, `name`, `subject`, `id`, `seq`)과 국내 사이트에서 흔한 축약형
(`…Nm`, `…Ttl`, `…Seq`)을 자동으로 찾습니다.

**API 주소는 어떻게 찾나요 — 사이트 진단 워크플로**

Actions 탭 → **사이트 진단** → Run workflow 에 주소를 넣고 실행하면, GitHub 이 그 페이지를
브라우저로 열어 **어떤 API 를 호출하는지, 그 응답에 어떤 목록이 들어 있는지** 정리해 줍니다.
직접 개발자도구를 열어 볼 필요가 없습니다. (로컬에서는
`python3 monitor.py diagnose <주소> --browser --network`)

### 예시: 이 저장소에 기본 등록된 한화 채용

한화 채용 페이지는 화면을 자바스크립트로 그리고, 공고가 링크(`<a>`)로 되어 있지 않아
브라우저로 렌더링해도 메뉴 링크만 잡힙니다. 진단으로 찾은 실제 목록 API 를 쓰도록 설정했습니다.

| 항목 | 값 |
| --- | --- |
| 주소 | `https://hwadm.hanwhain.com/new-backend/portal/api/rcRecruit/search-rcrt` |
| 확인 방식 / 요청 | JSON API / `POST` (본문에 `page`, `size` 등 검색 조건) |
| 목록 경로 | `data.list` |
| 제목 / 식별자 / 마감일 | `rtNm` / `rtSeq` / `rtAcptEndDttm` |
| 상세 주소 | `https://www.hanwhain.com/portal/apply/recruit/detail?rtSeq={rtSeq}` |

이 설정으로 공고 50건이 정상 수집되는 것을 GitHub Actions 에서 확인했습니다.

---

## 4. 내 컴퓨터에서 돌려 보기 (선택)

GitHub 없이도 같은 프로그램을 로컬에서 쓸 수 있습니다. 설치할 패키지는 없습니다.

```bash
cd job-monitor
python3 monitor.py serve --open              # 로컬 설정 화면 + 주기 확인 (http://localhost:8765)
python3 monitor.py check --no-email          # 지금 한 번 확인해서 결과만 출력
python3 monitor.py diagnose <주소>                     # 그 주소에서 무엇이 추출되는지 점검
python3 monitor.py diagnose <주소> --browser --network # 렌더링하고 목록 API 까지 추적
python3 monitor.py diagnose --site-json <파일>         # 설정 그대로(POST 본문 포함) 점검
python3 monitor.py list                      # 사이트와 마지막 확인 상태
python3 monitor.py test-email                # 테스트 메일 발송
```

로컬 설정 화면에서는 사이트별 **미리보기**·**지금 확인**·**기록 초기화** 도 쓸 수 있습니다.
비밀번호는 환경변수로 넘기세요(설정 파일에 저장하면 저장소에 올라갈 수 있습니다).

```bash
export JOBMON_SMTP_PASSWORD='앱비밀번호'
python3 monitor.py check
```

로컬에서 확인한 기록도 `job-monitor/data/state.json` 에 쌓입니다. GitHub 실행 기록과 섞이는 것이
싫으면 `--config` 로 다른 경로를 쓰세요.

---

## 5. 파일 구성

```
.github/workflows/job-monitor.yml            매시간 실행 (확인 → 메일 → 기록 커밋)
.github/workflows/job-monitor-diagnose.yml   사이트 진단 (주소를 넣고 수동 실행)
monitor/                            공개 설정 화면 (GitHub Pages)
  ├─ index.html
  └─ assets/{app.js,styles.css}     GitHub API 로 config.json 을 읽고 쓰는 화면
job-monitor/
  ├─ config.json                    설정 (설정 화면이 저장하는 파일)
  ├─ config.example.json            설정 예시
  ├─ data/state.json                이미 본 공고 기록 (워크플로가 커밋)
  ├─ data/last-run.json             마지막 실행 요약 (설정 화면 표시용)
  ├─ data/email-status.json         마지막 메일 발송 결과 (주소는 남기지 않음)
  ├─ monitor.py                     실행 파일 (serve / check / run / list / diagnose / test-email)
  ├─ tools/verify_config.py         설정 점검 + 비밀번호 유출 방지
  ├─ jobmon/
  │   ├─ config.py                  설정 읽기·쓰기·검증, 시크릿(환경변수) 우선 적용
  │   ├─ fetch.py                   페이지 내려받기 (+ 선택적 브라우저 렌더링)
  │   ├─ extract.py                 공고 목록 추출 (JSON-LD → 내장 JSON → 링크 → 본문 해시)
  │   ├─ store.py                   이미 본 공고 기억
  │   ├─ notify.py                  이메일 작성·발송
  │   ├─ runner.py                  확인 실행 + 주기 판단
  │   ├─ server.py                  로컬 설정 화면 서버
  │   └─ webui/                     로컬 설정 화면
  └─ tests/test_jobmon.py           테스트 36개 (인터넷 없이 로컬 임시 서버로 확인)
```

테스트 실행:

```bash
cd job-monitor && python3 -m unittest discover -s tests -v
```

---

## 6. 문제가 생기면

| 증상 | 확인할 것 |
| --- | --- |
| 설정 저장이 안 됨 | 토큰의 `Contents: Read and write` 권한, 저장소/브랜치 이름 |
| `지금 확인 실행` 이 403/404 | 토큰의 `Actions: Read and write` 권한, 워크플로가 기본 브랜치에 있는지 |
| 확인은 되는데 메일이 안 옴 | Actions 탭에서 `테스트 메일만 보내기` 로 실행해 원인 메시지를 확인 |
| 매번 같은 공고를 다시 알림 | Actions 의 **Read and write permissions** (기록 커밋 실패 시 발생) |
| 목록이 0개 / `fingerprint` | 위 3장 — 필터, 브라우저 렌더링, JSON API |
| 같은 공고가 두 번 메일로 옴 | 확인 기록 커밋이 실패했거나, 오래된 기록을 가진 브랜치에서 워크플로가 돌았을 때 생깁니다. Actions 탭에서 해당 실행의 브랜치와 `확인 기록 저장` 단계를 확인하세요. |
| 예약 실행이 자주 건너뜀 | GitHub 예약 실행은 보장되지 않습니다. 급하면 `지금 확인 실행` 을 쓰세요. |
| 가끔 `접속 실패: 응답 시간 초과` | 상대 서버가 느린 것입니다. 3회까지 자동으로 다시 시도하며, 그래도 실패하면 그 사이트만 건너뛰고 다음 주기에 다시 확인합니다. 자주 발생하면 확인 주기를 늘려 보세요. |
| 예약 실행이 멈춤 | Actions 탭에서 워크플로 **Enable**, 기본 브랜치 여부 |

실행 기록과 오류 메시지는 저장소 **Actions 탭 → 채용공고 확인** 에서 볼 수 있습니다.

---

## 7. 알아 두면 좋은 점

- 확인 주기를 너무 짧게 두지 마세요. 상대 서버에 부담이 되고 차단될 수 있습니다(24시간 권장).
- 로그인해야 보이는 공고, 캡차가 있는 사이트는 확인할 수 없습니다.
- 사이트가 화면 구조를 바꾸면 목록이 안 잡힐 수 있습니다. 가끔 설정 화면에서 상태를 봐 주세요.
- `fingerprint` 로 감시 중인 사이트는 공고와 무관한 변경(배너, 조회수 등)에도 알림이 올 수 있습니다.
