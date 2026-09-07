# 채용공고 모니터

지정한 채용 사이트를 **정해진 주기로 자동 확인**해서, 새로 올라온 공고가 있으면 **이메일로 알려 주는**
프로그램입니다. 확인할 사이트 주소 · 확인 주기 · 수신 이메일은 브라우저 설정 화면에서 바꿀 수 있습니다.

- 파이썬 표준 라이브러리만 사용합니다. **설치할 패키지가 없습니다.**
- 설정 화면(웹 UI)은 내 컴퓨터(`127.0.0.1`)에서만 열립니다.
- 기본 예시로 [한화 채용 페이지](https://www.hanwhain.com/portal/apply/recruit)가 등록되어 있습니다.

---

## 1. 빠른 시작

```bash
cd job-monitor
python3 monitor.py serve --open        # 설정 화면 + 주기 확인 함께 실행
```

- macOS / 리눅스: `start-mac-linux.sh` 더블클릭
- 윈도우: `start-windows.bat` 더블클릭

브라우저에서 <http://localhost:8765> 가 열립니다. 창을 켜 둔 동안 설정한 주기마다 자동으로 확인합니다.
종료는 터미널에서 `Ctrl+C`.

처음 실행하면 `config.json` 이 자동으로 만들어집니다.

### 설정 순서

1. **확인할 사이트** — 이름과 주소를 넣고 `미리보기` 를 눌러 공고 목록이 제대로 잡히는지 확인합니다.
2. **확인 주기** — 기본 24시간. 사이트별로 다르게 줄 수도 있습니다.
3. **알림 이메일** — 받는 주소를 넣고, 보내는 계정(SMTP)을 설정한 뒤 `테스트 메일 보내기` 로 확인합니다.
4. **설정 저장** → `지금 전체 확인` 으로 동작을 확인합니다.

> 사이트를 처음 확인할 때는 그때의 목록을 **기준**으로 저장만 하고 메일을 보내지 않습니다.
> (이미 올라와 있던 공고 수십 건이 한꺼번에 메일로 오는 것을 막기 위해서입니다.)
> 그 다음 확인부터 새로 올라온 글만 알려 줍니다. 첫 확인 결과도 받고 싶으면
> 설정 화면의 `처음 확인할 때 발견한 공고도 메일로 받기` 를 켜세요.

---

## 2. 이메일 설정 (Gmail 기준)

| 항목 | 값 |
| --- | --- |
| SMTP 서버 | `smtp.gmail.com` |
| 포트 / 보안 | `587` / STARTTLS |
| 아이디 | 본인 Gmail 주소 |
| 비밀번호 | Google 계정 **앱 비밀번호** (일반 로그인 비밀번호 아님) |

앱 비밀번호는 Google 계정 → 보안 → 2단계 인증을 켠 뒤 **앱 비밀번호**에서 만들 수 있습니다.
네이버는 `smtp.naver.com` / 465(SSL), 다음은 `smtp.daum.net` / 465(SSL) 를 쓰고,
메일 설정에서 SMTP 사용을 허용해야 합니다.

비밀번호를 파일에 저장하고 싶지 않다면 환경변수로 넘길 수 있습니다.

```bash
export JOBMON_SMTP_PASSWORD='앱비밀번호'
python3 monitor.py serve
```

`config.json` 은 비밀번호가 들어갈 수 있어 권한 600 으로 저장되고, git 에 올라가지 않도록
`.gitignore` 에 넣어 두었습니다.

---

## 3. 명령어

| 명령 | 하는 일 |
| --- | --- |
| `python3 monitor.py serve` | 설정 화면 + 주기 확인 (보통 이것만 쓰면 됩니다) |
| `python3 monitor.py check` | 지금 한 번 확인. cron / 작업 스케줄러용 |
| `python3 monitor.py check --due-only` | 확인할 때가 된 사이트만 확인 (자주 돌릴 때) |
| `python3 monitor.py check --no-email` | 메일 없이 화면에만 결과 출력 |
| `python3 monitor.py run` | 설정 화면 없이 주기 확인만 계속 실행 |
| `python3 monitor.py list` | 등록된 사이트와 마지막 확인 상태 |
| `python3 monitor.py diagnose <주소>` | 그 주소에서 무엇이 추출되는지 점검 |
| `python3 monitor.py test-email` | 테스트 메일 발송 |

공통 옵션: `--config <경로>` (설정 파일 위치), `serve --host/--port` (기본 `127.0.0.1:8765`).

### 컴퓨터를 켤 때마다 자동 실행하고 싶다면

**macOS / 리눅스 (cron, 매일 오전 9시)**

```cron
0 9 * * * cd /경로/job-monitor && /usr/bin/python3 monitor.py check >> data/cron.log 2>&1
```

**윈도우 (작업 스케줄러)**

- 프로그램: `python`, 인수: `monitor.py check`, 시작 위치: `job-monitor` 폴더

cron / 작업 스케줄러로 돌릴 때는 `serve` 대신 `check` 를 쓰면 됩니다. 실행할 때마다 한 번 확인하고 끝납니다.

---

## 4. 사이트 목록이 잡히지 않을 때

`미리보기` 또는 `diagnose` 결과의 **추출 방식**을 먼저 확인하세요.

| 추출 방식 | 의미 |
| --- | --- |
| `jsonld` | 페이지의 채용공고 구조화 데이터(JobPosting)에서 가져옴 — 가장 정확 |
| `embedded-json` | 페이지에 박혀 있는 JSON(`__NEXT_DATA__` 등)에서 가져옴 |
| `links` | 페이지의 링크 목록에서 가져옴 |
| `json` | 주소가 JSON API 인 경우 |
| `fingerprint` | 목록을 못 찾아서 **본문이 바뀌었는지만** 감시 |

### 방법 1 — 필터로 다듬기

`links` 로 잡히면 메뉴·푸터 링크까지 섞입니다. 세부 설정에서 정규식으로 걸러 주세요.

- 주소 필터: `recruit|notice` — 이 문자열이 포함된 링크만
- 제외 필터: `로그인|약관|개인정보` — 이런 링크는 제외

### 방법 2 — 브라우저 렌더링 (자바스크립트로 목록을 그리는 사이트)

한화 채용 페이지처럼 접속 후 자바스크립트로 목록을 그리는 사이트는 원본 HTML 에 공고가 없습니다.
이때는 확인 방식을 **브라우저 렌더링** 으로 바꾸세요. 한 번만 아래를 설치하면 됩니다.

```bash
pip install playwright
playwright install chromium
```

### 방법 3 — JSON API 직접 지정 (가장 안정적)

브라우저 개발자도구(F12) → **Network** 탭에서 페이지를 새로고침하면, 목록을 가져오는 요청이 보입니다.
그 주소를 사이트 주소로 넣고 확인 방식을 **JSON API** 로 바꾼 뒤 필드 이름을 지정합니다.

| 칸 | 예시 | 설명 |
| --- | --- | --- |
| JSON 목록 경로 | `data.list` | 공고 배열이 있는 위치 |
| 제목 필드 | `title` | 공고 제목 |
| 식별자 필드 | `id` | 같은 공고인지 구분할 값 |
| 상세 주소 형식 | `https://www.example.com/jobs/{id}` | `{필드명}` 자리에 값이 들어갑니다 |

비워 두면 흔한 이름(`title`, `name`, `subject`, `id`, `seq` …)을 자동으로 찾습니다.

---

## 5. 설정 파일 (`config.json`)

설정 화면에서 바꾸는 값이 그대로 저장됩니다. 직접 열어 고쳐도 됩니다. (`config.example.json` 참고)

| 키 | 설명 |
| --- | --- |
| `check_interval_hours` | 기본 확인 주기(시간) |
| `notify_on_first_run` | 첫 확인 결과도 메일로 받을지 |
| `recipients` | 수신 이메일 목록 |
| `email.*` | 보내는 계정(SMTP) 설정 |
| `sites[].mode` | `auto` · `html` · `json` · `browser` |
| `sites[].interval_hours` | 사이트별 주기 (비우면 기본값) |
| `sites[].url_pattern` / `title_pattern` / `exclude_pattern` | 정규식 필터 |
| `request.timeout_sec` | 접속 제한 시간 |

확인 기록은 `data/state.json`, 실행 로그는 `data/monitor.log` 에 쌓입니다.
`data/` 를 지우면 다음 확인 때 현재 목록을 새 기준으로 저장합니다.

---

## 6. 파일 구성

```
job-monitor/
├─ monitor.py             실행 파일 (serve / check / run / list / diagnose / test-email)
├─ config.example.json    설정 예시
├─ start-mac-linux.sh     더블클릭 실행용
├─ start-windows.bat      더블클릭 실행용
├─ jobmon/
│  ├─ config.py           설정 읽기·쓰기·검증
│  ├─ fetch.py            페이지 내려받기 (+ 선택적 브라우저 렌더링)
│  ├─ extract.py          공고 목록 추출 (JSON-LD → 내장 JSON → 링크 → 본문 해시)
│  ├─ store.py            이미 본 공고 기억 (data/state.json)
│  ├─ notify.py           이메일 작성·발송
│  ├─ runner.py           확인 실행 + 주기 스케줄러
│  ├─ server.py           설정 웹 UI + JSON API
│  └─ webui/              설정 화면 (index.html · styles.css · app.js)
└─ tests/test_jobmon.py   테스트 (인터넷 없이 로컬 임시 서버로 확인)
```

테스트 실행:

```bash
cd job-monitor && python3 -m unittest discover -s tests -v
```

---

## 7. 알아 두면 좋은 점

- 확인 주기를 너무 짧게 두지 마세요. 상대 서버에 부담이 되고 차단될 수 있습니다(기본 24시간 권장).
- 로그인해야 보이는 공고, 캡차가 있는 사이트는 이 프로그램으로 확인할 수 없습니다.
- 사이트가 화면 구조를 바꾸면 목록이 안 잡힐 수 있습니다. 이때는 `미리보기` 로 다시 맞춰 주세요.
- `fingerprint` 로 감시 중인 사이트는 공고와 무관한 변경(배너, 조회수 등)에도 알림이 올 수 있습니다.
