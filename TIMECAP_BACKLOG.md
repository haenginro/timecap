# TIMECAP 개선 백로그
> 작성자: Claire (Claude Sonnet 4.6)
> 작성일: 2026-05-17
> 대상: TimeCap macOS 메뉴바 앱
> 문서유형: 백로그 / 점검 기준서
> 위치: 리포 루트 TIMECAP_BACKLOG.md

---

## 문서 목적

타임캡 운영 중 발견된 개선사항과 미결 항목을 단일 문서로 통합 관리한다.
코코(Claude Code)와 클래어(Claude Web) 공통 참조 기준점으로 사용한다.
TIMECAP.md 계약서는 건드리지 않는다 — 이 문서는 별도 관리.

---

## 상태 기호

- ✅ 완료
- 🔄 진행 중
- ⏳ 대기
- ❌ 보류 / 스킵

---

## Priority 1 — 필수 수정 (완료)

| # | 항목 | 상태 |
|---|------|------|
| P1-01 | `frame_count` 불일치 — 실제 파일 수 기반 초기화로 수정 | ✅ |
| P1-02 | Archive 후 상태 미동기화 — `_sync_menu()` 연동 완료 | ✅ |
| P1-03 | `capture_tick` 실패 시 `self.title` 직접 설정 제거 → `_sync_menu` 통합 | ✅ |

---

## Priority 2 — 안정성 강화

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| P2-01 | `ffmpeg` 임시파일 충돌 — `_tc_filelist.txt` 고정 → 타임스탬프 기반 또는 `tempfile` 전환 | ✅ | `tempfile.NamedTemporaryFile` 적용 확인 (2026-07-24) |
| P2-02 | 파일 경로 `escape` 안전성 — `concat` 리스트 특수문자 처리 | ✅ | 이스케이프 형식 오류(`'\\''`) 발견·수정 → `'\''` (2026-07-24) |
| P2-03 | `archive` 이동 방식 — `os.rename()` → `shutil.move()` 전환 | ✅ | 적용 확인 (2026-07-24) |

---

## Priority 3 — 사용성 개선

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| P3-01 | Make Video 동작 명확화 — 생성 중 진행 메시지/인디케이터 표시, 완료 후 자동 소멸 | ⏳ | BL-01 통합 |
| P3-02 | 상태 표시 체계 개선 — capture 상태 / process 상태 분리 | ⏳ | |
| P3-03 | 무활동 자동 일시정지 — 입력·캡처 없는 상태 지속 시 화면보호기 모드 진입 | ⏳ | BL-02 통합 |

---

## Priority 4 — 미결 구조 항목

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| P4-01 | 로그 시스템 구현 — `logs/tc_YYYYMMDD.log` / Start·Stop·캡처·영상·아카이브 기록 | ✅ | v1.2에서 CAPTURE_FAIL/TIMEOUT/SKIP 기록 구현 확인 (2026-07-24) |
| P4-02 | `Quit` 메뉴 — 문서·코드 일관성 맞추기 | ⏳ | |

---

## Priority 5 — 패키징 / 배포

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| P5-01 | 패키징 앱 화면 캡처 불가 — Apple 보안 정책 추정, Developer ID 서명 + Notarization 적용 | ❌ | ⚠️반증 2026-07-24 🔬: 미서명 py2app 번들도 TCC 화면기록 권한 부여 시 macOS 26.5에서 캡처 정상. 서명/공증은 외부 배포(Gatekeeper) 목적으로만 필요 |
| P5-02 | Apple Developer Program 가입 ($99 납부) | ⏳ | 6월 초 목표 |
| P5-03 | App Store Connect 등록 및 심사 제출 | ⏳ | P5-01·02 완료 후 |
| P5-04 | config.json 위치 이전 — 소스 옆 → `~/Library/Application Support/TimeCap/` | ⏳ | 번들 실행 시 앱 내부 Resources에 설정을 쓰는 구조 = 서명 배포 시 서명 무효화. 서명 전 필수 (2026-07-24 발견) |

---

## 미래 방향 메모 (코드 변경 아님)

| 항목 | 내용 |
|------|------|
| 심플 / 헤비 분기 | `main` 심플 버전(App Store 출시용) / `timecap-pro` 브랜치(헤비 UI) |
| 윈도우 이식 | `rumps` → `pystray` / `screencapture` → `PIL ImageGrab` / `open` → `os.startfile()` |
| 앱 철학 | 로컬 퍼스트, 데이터 소유권 사용자 귀속, Naked Code 투명성 유지 |
| 장기 비전 | 화면 캡처 → 작업자 히스토리 기록 → 작업 패턴 분석 (옵시디언 포지셔닝) |

---

## 6월 출시 전 체크리스트

- [ ] P2-01 ~ P2-03 안정성 항목 완료
- [ ] P3-01 진행 표시 구현 (BL-01)
- [ ] P3-03 무활동 중지 구현 (BL-02)
- [ ] P4-01 로그 시스템 최소 구현
- [ ] P5-01 Developer ID 서명 + Notarization 완료
- [ ] P5-02 Apple Developer Program $99 납부
- [ ] 앱 설명문 한/영 작성
- [ ] App Store 스크린샷 준비
- [ ] 버전 `1.0.0` 확정
- [ ] `TIMECAP.md` 최신화

---

> Claude Sonnet 4.6 (Claire) 작성 — 2026-05-17
