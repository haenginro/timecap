# TimeCap
> macOS screen timelapse capture & video generator
> dev name: TimeCap | release candidate: Loopshot
> created: 2026-04-04

---

## core concept

- 간단하고 명확하게, 예외 없이
- 레고 블록 구조 — 각 블록은 독립 실행 가능
- 효율보다 로직 파악 우선 — 초중급자도 읽을 수 있는 심플 코드
- 파일명에 핵심 정보 항상 포함 — 폴더 밖으로 꺼내도 의미 유지
- 블록 간 데이터는 파일 시스템이 담당, 메모리 공유 없음

## naked code

화면 캡처 앱은 접근 권한이 필요하다 — 사용자의 신뢰가 전제조건.
TimeCap은 코드를 숨기지 않는다. 메뉴에서 소스를 바로 열 수 있고,
AI에게 던져서 30초 만에 검증 가능한 수준의 심플함을 유지한다.

```
TimeCap — Naked Code

· captures screen every 10s via screencapture
· saves to ~/timecap/ only
· no network calls
· no external data transfer
· source: View Source → verify yourself or ask AI

permissions required:
· Screen Recording — screencapture 캡처용
· Notifications    — 완료 알림용
· Disk             — ~/timecap/ 저장용
```

---

## Data Flow (핵심 흐름)

```
Capture       → Start Capture 시 실행 → screenshots/ 저장 → frame_count ↑
Make Video    → 사용자 실행 → screenshots/ 스냅샷 → ffmpeg → videos/ 생성 → screenshots/ 유지
Archive Move  → 사용자 실행 → screenshots/ → archive/YYYYMM/ 이동 → frame_count ↓
Log           → 캡처 실패 시 자동 기록 (passive)
```

> Make Video 와 Archive Move 는 의도적으로 분리된 독립 블록
> 영상 생성 후 Archive 는 사용자가 직접 실행 — 레고 블록 원칙

---

## Typical Workflow (운영 시나리오)

```
1. Start Capture        캡처 시작 → 10초 간격 자동 촬영
2. 일정 시간 후         메뉴바 ● TC N 으로 장 수 확인
3. Make Video           30장 이상일 때만 실행 가능 → 영상 생성
4. Archive Move         사용된 jpg → archive/YYYYMM/ 이동
5. logs 확인            캡처 실패 이력 확인 (필요 시)
6. 상태 초기화          screenshots/ 비움 → 다음 캡처 준비 완료
```

---

## Error Handling

| 상황 | 원인 | 해결 |
|------|------|------|
| 캡처 실패 (`! TC`) | Screen Recording 권한 없음 | 시스템 설정 → 화면 기록 → TimeCap 허용 |
| ffmpeg 실패 | ffmpeg 미설치 또는 경로 오류 | `brew install ffmpeg` |
| Not enough frames | 30장 미만 | 더 촬영 후 재시도 |
| Archive Move 없음 | screenshots/ 비어있음 | 캡처 후 실행 |

---

## 1. 현재 상태 (2026-04-05 기준)

| 항목 | 상태 |
|------|------|
| 캡처 간격 | ✅ 10초 (변경 완료) |
| 구조 | ✅ 레고 블록 설계 |
| 기능 | ✅ 실전 투입 가능 수준 |
| 운영 안정성 | ✅ Phase 2 완료 |
| 패키징 | ✅ TimeCap.app v1.1 |
| GitHub | ✅ haenginro/timecap (public, 2026-07-24) |

---

## 2. 기술 스택

| 항목 | 선택 | 이유 |
|------|------|------|
| UI | `rumps` | macOS 메뉴바 앱, 외부 앱 불필요 |
| 캡처 루프 | `rumps.timer` | while+sleep 대체, 프로세스 관리 내장 |
| 영상 생성 | `ffmpeg` (subprocess) | 기존 방식 유지 |
| 비동기 처리 | `threading` | ffmpeg 실행 중 UI 블로킹 방지 |
| 패키징 | `py2app` | `.app` 번들 생성 |

```bash
pip3 install rumps       # macOS 는 pip 아닌 pip3
pip3 install py2app      # 패키징 시에만
```

---

## 3. 디렉토리 구조

> 코드 위치는 리포 클론 위치(어디든 무관), **저장 폴더는 사용자 지정 가능(기본 `~/timecap/`)**.
> 메뉴 `📁 Storage`에서 변경 → `config.json`에 영속. 변경 직후부터 새 폴더 사용, 기존 데이터는 그대로 보존.

```
timecap/                      ← 리포 루트 = 코드/설정 위치
  ├─ TimeCap.py              ← 메인 앱 소스
  ├─ TIMECAP.md              ← 설계 문서 (이 파일)
  ├─ config.json             ← 사용자 설정 (base_dir 포함, 코드 옆 고정)
  ├─ setup.py                ← py2app 패키징 설정
  ├─ run.sh                  ← 터미널 실행 스크립트
  ├─ .gitignore
  ├─ dist/
  │    └─ TimeCap.app        ← 완성된 앱 번들 (24.7MB)
  └─ build/                  ← 패키징 임시 파일 (삭제 가능)

<base_dir>/                   ← 사용자 지정 저장 폴더 (기본 ~/timecap/)
  ├─ screenshots/            ← 캡처 임시 저장소
  ├─ videos/                 ← 생성된 영상 보관
  ├─ archive/
  │    └─ YYYYMM/            ← 월별 jpg 보관
  └─ logs/
       └─ tc_YYYYMMDD.log    ← 일자별 작업 이력
```

---

## 4. 네이밍 규칙

| 대상 | 규칙 | 예시 |
|------|------|------|
| 캡처 파일 | `tc_YYYYMMDD_HHMMSS.jpg` | `tc_20260404_143022.jpg` |
| 영상 파일 | `tc_YYYYMMDD_HHMMSS_Xfps.mp4` | `tc_20260404_033706_4fps.mp4` |
| 아카이브 폴더 | `YYYYMM/` | `202604/` |
| 로그 파일 | `tc_YYYYMMDD.log` | `tc_20260404.log` |
| ffmpeg 임시 파일 | `_tc_HHMMSS_filelist.txt` | 타임스탬프 기반, 충돌 방지 |

---

## 5. 레고 블록 구조

```
[Block 1] Capture       캡처 루프 Start / Stop
[Block 2] Make Video    스냅샷 → ffmpeg → mp4 생성
[Block 3] Archive Move  사용된 jpg → archive/YYYYMM/ 이동
[Block 4] Share Video   생성된 mp4 공유/전송 (future)
[Block 5] Log           캡처 실패 기록 ✅ 부분 구현
[Block 6] Archive Clean archive/ 내부 정리 (future)
```

> Block 5 현재 상태:
> - logs/ 폴더 생성 ✅
> - 캡처 실패 시 로그 기록 ✅
> - 영상 생성 / 아카이브 이력 기록 → future

---

## 6. 메뉴바 인디케이터

| 상태 조합 | 메뉴바 표시 |
|-----------|-------------|
| 대기 | `TC` |
| 캡처 중 | `● TC 42` |
| 영상 생성 중 | `⏳ TC` |
| 캡처 중 + 영상 생성 중 | `⏳● TC 42` |
| 오류 | `! TC` |
| 오류 + 캡처 중 | `! ● TC 42` |

---

## 7. 메뉴 구성

```
● TC 42                       ← status (not clickable)
─────────────────
⏱ Capture Interval [10s]
⏸ Idle Threshold [5m]
🎭 Idle Mode [skip]
🔔 Sound [ON]
📁 Storage [~/timecap]        ← 저장 폴더 선택 (osascript choose folder)
─────────────────
Start Capture
Stop Capture
─────────────────
Make Video
Archive Move
─────────────────
Open Screenshots
Open Videos
Open Archive
─────────────────
View Source
What this app does
─────────────────
Quit
```

---

## 8. Make Video 상세 로직 (Block 2)

1. `is_processing` 플래그 확인 — 중복 실행 방지
2. `screenshots/*.jpg` 목록 스냅샷
3. 시작 알림: "N장 기준 영상 생성 / 캡처 상태 표시"
4. ffmpeg 실행 (threading)
5. `videos/tc_YYYYMMDD_HHMMSS_4fps.mp4` 생성
6. 완료 시 macOS 알림 발송 + `_sync_menu()`
7. 최소 프레임 30장 미만 시 실행 거부

---

## 9. Archive Move 상세 로직 (Block 3)

1. `is_processing` 플래그 확인 — 중복 실행 방지
2. `screenshots/*.jpg` 목록 확인
3. 파일명에서 날짜 추출 → `archive/YYYYMM/` 자동 생성
4. `shutil.move()` 로 이동 (크로스 디바이스 대비)
5. 완료 후 `frame_count` 재계산 + `_sync_menu()` 실행

---

## 10. GitHub 관리

### 저장소
```
URL       https://github.com/haenginro/timecap
접근      Public (2026-07-24 공개)
branch    main
```

### 커밋 이력
| 커밋 | 내용 |
|------|------|
| cfeb79f | TimeCap v1.0 — Phase 1 complete |
| bd66be8 | TimeCap v1.1 — Phase 2 improvements |

### .gitignore 규칙
```
screenshots/*.jpg      ← 캡처 파일 제외
videos/*.mp4           ← 영상 파일 제외
archive/**/*.jpg       ← 아카이브 제외
_tc_*_filelist.txt     ← ffmpeg 임시파일 제외
.DS_Store
.claude/
```

### 코드 수정 후 push 순서
```bash
cd ~/timecap
git add .
git commit -m "커밋 메시지"
git push
```

---

## 11. 패키징 (py2app)

### TimeCap.app 구조
```
TimeCap.app/                    ← 배포 단위 (이것만 전달)
  Contents/
    MacOS/
      TimeCap                   ← 실행 바이너리
    Resources/
      lib/python3.9/
        ├─ rumps/               ← rumps 내장
        └─ lib-dynload/         ← Python 표준 라이브러리
      site.pyc                  ← 소스 컴파일본
    _CodeSignature/
      CodeResources
```

### 폴더 역할
| 폴더/파일 | 역할 | 배포 시 |
|-----------|------|---------|
| `dist/TimeCap.app` | 완성된 앱 번들 | ✅ 필요 |
| `build/` | 패키징 임시 파일 | ❌ 삭제 가능 |
| `setup.py` | 패키징 설정 | 개발용만 보관 |

### 재패키징 순서
```bash
cd ~/timecap

# 기존 빌드 정리
rm -rf build/ dist/

# 패키징 실행
python3 setup.py py2app

# 결과 확인
ls -lh dist/TimeCap.app
```

### 권한 설정 (첫 실행 시)
```
Screen Recording 권한 필요
시스템 설정 → 개인정보 보호 및 보안 → 화면 기록
→ TimeCap 허용
→ Quit & Reopen
```

---

## 12. 개발 단계

### Phase 1 — core ✅
- [x] `TimeCap` 기본 구조
- [x] 메뉴바 UI + 인디케이터
- [x] `rumps.timer` 캡처 루프
- [x] Start / Stop
- [x] Make Video (Block 2)
- [x] Archive Move (Block 3)
- [x] Naked Code 메뉴 (View Source / What this app does)
- [x] ffmpeg 동적 탐지
- [x] is_processing 중복 실행 방지
- [x] 실제 영상 생성 확인 (2MB · 2560x1440)

### Phase 2 — 운영 안정화 ✅
- [x] frame_count 실제 파일 수 기준 초기화
- [x] Archive 완료 후 frame_count 재계산 + _sync_menu()
- [x] 캡처 실패 로그 + fail_count + 알림 1회
- [x] ffmpeg 임시파일 타임스탬프 기반
- [x] concat 경로 특수문자 escape
- [x] os.rename() → shutil.move() 교체
- [x] Make Video 알림 문구 개선
- [x] 상태 표시 체계 개선 (err + proc + cap 조합)
- [x] CAPTURE_INTERVAL 15 → 10초
- [x] GitHub push (bd66be8)
- [x] py2app 패키징 (TimeCap.app v1.1 · 24.7MB)

### Phase 3 — 기능 확장 (예정)
- [ ] 권한 안내 팝업 (첫 실행 시)
- [ ] 캡처 실패 알림 문구 개선
- [ ] What this app does 권한 안내 추가
- [ ] Share Video (Block 4)
- [ ] 로그 시스템 확장 (Block 5)
- [ ] Archive Clean (Block 6)
- [x] 캡처 간격 설정 메뉴
- [x] 저장 폴더 지정 (v4 · 2026-05-24)
- [ ] 커스텀 앱 아이콘
- [ ] 로그인 자동 시작 (launchd)
- [ ] Windows 이식 검토

---

## 13. 사용 제한 (현재)

- 저장 폴더(`base_dir`)는 메뉴 `📁 Storage`에서 변경 가능 — 외부 볼륨 / NAS 지원 (v4 · 2026-05-24)
  - 단, 쓰기 권한 검증 통과해야 적용. 권한 없으면 알림 후 롤백
  - `config.json`은 코드 옆 고정(리포 루트의 `config.json`) — 저장 폴더 따라가지 않음
- macOS 전용 (rumps, screencapture)
- 개인 운영 도구 — 대중 배포 목적 아님

---

## 14. Windows 이식 검토 (future)

| 항목 | macOS | Windows 대안 |
|------|-------|-------------|
| 메뉴바 UI | `rumps` | `pystray` |
| 화면 캡처 | `screencapture` | `PIL.ImageGrab` |
| 폴더 열기 | `open` | `os.startfile()` |
| ffmpeg | 동일 | 동일 ✅ |

---

## 15. 미결 사항

| 항목 | 내용 |
|------|------|
| Make Video 후 동작 | 캡처 자동 Stop vs 계속 진행 — 사용 패턴 보고 결정 |
| Archive Move 설정 | Make Video 완료 후 → A.자동이동 / B.수동 / C.완료 후 물어보기(추천 디폴트) 첫 실행 시 선택, 설정 메뉴에서 변경 가능하도록 구현 예정 |
| archive_move 🎬 미표시 | 같은 볼륨 이동이라 순간 완료 → 깜빡임이 오히려 어색 → 의도적 제외. v4에서 외부 볼륨 base_dir 지원되며 같은 base_dir 내부 이동은 여전히 같은 볼륨 가정 — 다른 볼륨 간 이동은 비대상 |
| Share Video 방식 | AirDrop / 메일 / 폴더 복사 등 검토 필요 |
| 앱 아이콘 | 기본 Python 아이콘 → 커스텀 아이콘 교체 |
| Applications 설치 | /Applications/ 이동 여부 결정 |
| 배포명 | Loopshot 확정 시 rename |

---

## 16. 테스트 이력

| 일자 | 항목 | 결과 |
|------|------|------|
| 2026-04-04 | rumps 설치 | pip3 install rumps (pip 아님 주의) |
| 2026-04-04 | 앱 실행 | 메뉴바 TC 정상 표시 |
| 2026-04-04 | 캡처 간격 | 15초 ± 0초 — 45장 전수 확인 |
| 2026-04-04 | 30장 미만 방어 | Not enough frames 알림 정상 |
| 2026-04-04 | Make Video | tc_20260404_033706_4fps.mp4 · 2MB · 2560x1440 |
| 2026-04-04 | Naked Code | What this app does 팝업 정상 |
| 2026-04-05 | 1000장 Archive Move | 순간 완료 (같은 볼륨 이동) |
| 2026-04-05 | GitHub push | bd66be8 — main |
| 2026-04-05 | py2app 패키징 | TimeCap.app v1.1 · 24.7MB · 더블클릭 실행 확인 |

---

## 17. 멀티 Claude 워크플로우

```
클래어 (웹 Claude)       → 설계 / 총괄 / 점검 / TIMECAP.md 관리
코코 (Claude Code)       → 코드 수정 / 실행 / 디버깅 / GitHub push
버디 (Dragon · Uncommon) → 코코 옆 상주, 날카로운 한 마디
앨리 (ChatGPT)           → 크로스 점검 (필요 시)
```

### 오더 원칙
```
1. 목적지 먼저, 디테일 나중
2. TIMECAP.md 읽으라고 먼저 언급
3. TIMECAP.md 는 건드리지 마 명시
4. 완료 기준 항상 포함
5. 한 번에 하나씩 — 헛둘헛둘
```
