# Delos Lab

[Virtual Consensus in Delos (OSDI 2020)](https://www.usenix.org/system/files/osdi20-balakrishnan.pdf)의
핵심 분해를 코드와 프로세스 장애로 따라가는 로컬 학습 구현이다. VirtualLog가 서로 다른
Loglet을 하나의 가상 주소 공간으로 연결하고, Paxos 기반 MetaStore가 버전된 LogChain을
결정하며, KV 애플리케이션이 로그를 재생해 상태를 구체화한다.

이 저장소는 Meta의 Delos 전체 구현이나 운영용 데이터베이스가 아니다. LogChain 재구성,
Converged NativeLoglet, 고정 membership Paxos MetaStore와 교육용 SQLite KV까지를 명시적인
범위로 삼는다. 논문과 정확히 같은 부분, 단순화한 부분, 제외한 부분은
[논문-구현 대조표](docs/paper-implementation-map.md)에 기록되어 있다.

> **Project status:** 교육·실험용 `0.1.x` 구현이다. 인증, TLS, 운영 failure detector,
> 온라인 MetaStore membership 변경을 제공하지 않으며 실제 데이터를 맡기는 용도가 아니다.

## 문서

- [문서 안내](docs/README.md): 목적별 문서와 권장 읽기 순서
- [개념 지도](docs/learning-guide.md): 용어, 계층, 재구성과 한 PUT의 전체 흐름
- [코드 워크스루](docs/code-walkthrough.md): 파일 읽기 순서와 PUT·GET·재구성·노드 편입 호출 경로
- [아키텍처](docs/architecture.md): 패키지 소유권, 런타임 배치와 의존성 규칙
- [논문-구현 대조표](docs/paper-implementation-map.md): 구현·축소·제외 범위
- [테스트 학습 지도](docs/test-scenarios.md): 테스트를 분산시스템 계약으로 읽는 방법
- [설계 결정](docs/design-decisions.md): subprocess, Converged 배치와 제어면 경계의 이유
- [형식 모델](formal/README.md): TLA+ 모델의 범위와 TLC 실행 방법

## Quick Start

### 요구 사항

- macOS 또는 Linux
- [mise](https://mise.jdx.dev/) `2026.2.13+`

`mise.toml`이 Python `3.14.7`, Node.js `22.23.2`, uv를 관리한다. uv는 잠금 파일
`uv.lock`에 따라 프로젝트 `.venv`와 Python 의존성을 관리한다. 시스템에 Python, Node.js,
uv 또는 npm을 따로 맞출 필요가 없다.

### 설치

```bash
git clone https://github.com/rscarrera27/delos-lab.git
cd delos-lab
mise trust
mise run setup
```

`mise trust`는 clone한 프로젝트 설정을 검토한 뒤 최초 한 번만 실행한다. `mise run setup`은
필요한 도구를 자동 설치하고, uv로 잠긴 Python 개발 환경을 동기화한 뒤 frontend 의존성과
production bundle을 준비한다. `frontend/dist`를 Controller가 제공하므로 별도의 프론트엔드
개발 서버나 Docker는 필요하지 않다. 사용 가능한 명령은 `mise tasks`로 확인한다.

### 실행

```bash
mise run lab
```

브라우저에서 [http://127.0.0.1:9400](http://127.0.0.1:9400)을 연다. Controller는
MetaStore 프로세스 3개를 먼저 시작한 뒤 DB 프로세스 3개를 시작하고, 빌드된 SPA와 API를
같은 주소에서 제공한다. 다음 명령으로 Controller 응답을 확인할 수 있다.

```bash
curl -fsS http://127.0.0.1:9400/api/health
```

종료할 때는 Controller 터미널에서 `Ctrl+C`를 누른다. Controller가 자신이 시작한 여섯
하위 프로세스도 함께 종료한다.

## 첫 번째 재구성 실험

1. `KV Console`에서 DB 하나를 골라 `PUT`을 실행한다. 첫 요청이 초기 LogChain을 만든다.
2. `Topology`의 Database > NativeLoglet 카드에서 활성 sequencer를 확인한다.
3. Database > Process 테이블에서 그 DB 프로세스를 `Pause`한다. `SIGSTOP`으로 프로세스는
   유지되지만 응답하지 않아 timeout과 재구성을 관찰할 수 있다.
4. `KV Console`에서 살아 있는 다른 DB를 골라 `INCREMENT` 또는 `PUT`을 실행한다.
5. 기존 Loglet이 seal되고 새 sequencer를 가진 segment가 LogChain 끝에 설치되는지
   `Virtual Log`에서 확인한다.

NativeLoglet 내부에서 leader election을 하는 것이 아니다. 요청을 받은 DB가
`seal -> checkTail -> MetaStore CAS`를 수행하고 새 NativeLoglet으로 원래 명령을
재시도한다.

## Database 노드 추가

`Topology`의 Database > Process 테이블 하단 `Add database process`는 빈 Converged
프로세스를 하나 추가한다. 이 작업은
서로 다른 두 membership을 한 번에 바꾸지 않는다.

1. 기존 Database replica가 KV 값, 요청 중복 제거 기록과 적용된 VirtualLog 위치를 같은
   SQLite 트랜잭션에서 snapshot으로 내보낸다.
2. 새 노드가 snapshot을 원자적으로 설치하고 그 이후 VirtualLog를 현재 tail까지 재생한다.
3. 준비된 노드의 NativeLoglet 계층이 `reconfigExtend`를 실행한다.
4. MetaStore CAS로 설치된 새 segment부터 새 노드가 LogServer이자 sequencer 후보로
   참여한다. 과거 sealed segment의 저장 membership은 바뀌지 않는다.

Database의 프로토콜 멤버 ID와 OS 프로세스 ID는 `db-`와 5자리 소문자·숫자로 이루어진
하나의 불투명 ID(예: `db-rand0`)다. `Kill`은 이 ID를 Process 목록에서 제거하고 재사용하지
않는다. 이후 추가한 프로세스는 새 ID를 받으며, 새 NativeLoglet segment에서 제거된 멤버를
교체한다. 과거 LogChain은 이전 ID를 계속 참조하므로 Controller manifest에는 endpoint
해석을 위한 tombstone이 남는다.

같은 작업은 API로도 실행할 수 있다.

```bash
curl -fsS -X POST http://127.0.0.1:9400/api/database-nodes
```

3개에서 4개 LogServer로 확장하면 엄격한 과반은 3개다. 이는 안전하지만 장애 허용 수는
여전히 1이므로, 노드 하나를 더 추가해 5개가 되어야 2개 장애를 허용한다. 홀수 크기는
효율적인 배치 선택이지 majority quorum의 안전성 조건이 아니다.

## 구성과 코드 경계

```text
KV application
    │ append/read global positions
    ▼
VirtualLog ── CAS/read ──> MetaStore interface
    │                         └─ Paxos implementation
    │ opaque Loglet config
    ▼
Loglet adapter
    └─ NativeLoglet implementation

Lab Controller ── process lifecycle + read-only observation + browser proxy
Browser UI     ── projection only
```

- `virtual_log`는 Loglet의 `kind`, 설정 버전과 불투명 parameters만 저장한다.
  NativeLoglet의 storage member나 sequencer 필드를 알지 않는다.
- LogSegment는 반개구간 `[virtual_start, virtual_stop)`을 사용한다. 마지막 활성 segment만
  `virtual_stop = null`이다.
- `native_loglet`은 설정 해석, sequencer, 저장 정족수, `seal`, `checkTail`과 VirtualLog
  adapter를 소유한다.
- `virtual_log.metastore`는 버전 기반 CAS 포트를 정의하고 `metastore.paxos`가 이를
  구현한다.
- `kv.service`는 공통 VirtualLog와 초기 Loglet 설정 계약만 사용한다. sealed chain
  roll-forward와 Loglet 교체는 VirtualLog이 담당하고, NativeLoglet 정책의 조립은
  `runtime.converged`에 한정된다.
- Controller와 UI는 합의, append, seal 또는 LogChain 결정에 참여하지 않는다. UI의
  최신 체인은 Controller가 읽은 관측 투영이지 별도의 권위가 아니다.

DB 프로세스는 의도적으로 Converged 배치다. Application, VirtualLog client,
NativeLoglet client, LogServer 저장소와 해당 segment의 sequencer가 같은 프로세스 장애
단위를 이룬다. MetaStore Paxos peer는 별도 프로세스다.

## UI에서 관찰하는 것

- `Topology`: Database와 MetaStore를 각각 프로토콜 카드와 Process 카드로 나누어 보여
  준다. NativeLoglet 카드에서는 sequencer 설정과 최신 segment의 LogServer 상태를
  분리한다.
- `Virtual Log`: LogChain의 반개구간과 LogServer에서 관측한 물리적 entry copy
- `KV Console`: 선택한 DB 프로세스로 보내는 PUT, GET, DELETE, CAS, INCREMENT

각 카드는 우측 화살표로 접고 펼칠 수 있다. 접기는 표시만 바꾸므로 KV 입력값과 화면의
선택 상태를 폐기하지 않는다.

`Resume`/`Pause`는 같은 OS 프로세스에 `SIGCONT`/`SIGSTOP`을 보낸다. `Kill`은 Database
프로세스와 해당 identity를 퇴역시키며 MetaStore에는 제공되지 않는다. 따라서 이 제어면이
고정 Paxos membership을 변경하지 않는다. `running`은 OS 프로세스 수명주기이고
`reachable`은 Controller가 상태 응답을 읽었다는
뜻이다. 둘 다 정족수 확보나 KV 요청 성공을 보장하지 않는다. Controller polling은 상태를
전진시키지 않으며, 유휴 DB는 뒤처졌다가 그 DB에 요청이 들어오면 로그를 재생한다.

## 데이터와 실행 옵션

SQLite 데이터, WAL, manifest와 프로세스 로그는 `--runtime-dir` 아래에 유지된다. 같은
경로로 다시 실행하면 이전 데이터를 사용한다. UI의 `초기화`는 Controller가 관리하는
프로세스를 종료하고 이 파일만 삭제한 뒤, 시작 시 지정한 규모로 새 클러스터를
부트스트랩한다.

주요 옵션은 다음과 같다.

```bash
mise run lab -- --help
mise run lab -- --port 9500
mise run lab -- --no-auto-start
mise run lab -- --runtime-dir .runtime/five --meta-nodes 5 --db-nodes 5
```

MetaStore와 최초 DB node 수는 각각 3 이상이어야 한다. 과반 정족수는 전체 수의 절반보다
큰 값이다. 이미 만들어진 runtime에 지정한 초기 수보다 적은 node만 저장돼 있으면 시작을
거부한다. UI/API로 추가되었거나 제거된 Database identity의 이력은 manifest에 유지된다.

### LAN에서 열기

```bash
mise run lab -- \
  --host 0.0.0.0 \
  --port 9400
```

이 랩에는 인증, 권한 관리와 TLS가 없다. `0.0.0.0`은 모든 네트워크 인터페이스에
Controller와 프로세스 제어 API를 공개하므로 신뢰하는 사설 LAN에서만 사용해야 한다.
공용 Wi-Fi, 포트 포워딩 또는 인터넷 공개 환경에서는 사용하지 않는다.

## 개별 데모

```bash
mise run demo:loglet
mise run demo:virtual-log
mise run demo:paxos
mise run demo:kv
```

데모는 Docker 없이 인메모리 전송 또는 독립 loopback subprocess를 사용한다.

## 검증

Python·frontend 구현과 경계 검증:

```bash
mise run check
```

`check`는 ruff, format check, mypy, TypeScript typecheck, Python·frontend unit test와
production build를 순서대로 실행한다. 개별 그룹도 실행할 수 있다.

```bash
mise run quality
mise run test
mise run build
```

E2E는 Chromium을 한 번 설치한 뒤 실행한다. E2E가 자체 Controller를 9400번 포트에
시작하므로 같은 포트의 수동 랩은 먼저 종료해야 한다.

```bash
mise run setup:e2e
mise run test:e2e
```

의존성 경계 테스트는 VirtualLog의 NativeLoglet/Controller 의존과 Delos 코어의
Controller 의존을 금지한다. non-Native 메모리 adapter 테스트는 VirtualLog가 Loglet
설정을 실제로 불투명하게 다루는지도 확인한다.

TLA+ 모델의 범위와 TLC 실행 방법은 [formal/README.md](formal/README.md)에 있다.
VirtualLog 모델은 extend/trim/truncate/sealed-modify의 주소와 CAS 불변식을 검사하고,
NativeLoglet 모델은 append quorum, retry, seal, zombie repair와 trim을 검사한다. Paxos 전체의
형식 증명은 주장하지 않는다.

## 구현된 핵심 의미론

- NativeLoglet sequencer의 단조 위치 할당과 `(command_id, payload)` 멱등 재시도
- 정족수 성공 또는 seal까지 요청 수명과 독립적으로 유지되는 pending append
- open `checkTail`의 LogServer local-tail-or-seal notification
- LogServer 과반 append, `knownTail`, `seal`, 5-state `checkTail`과 sealed repair
- `seal -> tail 확정 -> LogChain CAS` 순서의 재구성
- `prefixTrim`과 `reconfigTruncate`/`reconfigModify`를 포함한 Figure 2 전체 API
- sealed NativeLoglet configuration 교체 전 데이터·trim watermark 준비와 검증
- sealed Loglet 뒤에 새 Loglet을 연결하는 연속 VirtualLog 주소 공간
- 닫힌 가상 범위 밖으로 격리되는 zombie entry
- 독립 Classic Paxos 슬롯의 Prepare, Accept, Decide와 barrier read
- Paxos promise, accepted/decided value와 적용 위치의 SQLite 영속화
- sparse Loglet을 보존하는 범위 기반 `readNext`와 KV replay
- KV 요청 중복 제거, 요청 경로 catch-up과 sequencer 장애 후 failover
- 원자적 application snapshot, VirtualLog replay와 새 Database replica bootstrap
- 새 segment를 통한 NativeLoglet LogServer membership 편입

Paxos proposer와 NativeLoglet sequencer는 서로 다른 역할이다. proposer는 MetaStore 명령
하나를 특정 Paxos 슬롯에 결정하고, sequencer는 특정 NativeLoglet 내부에서 append 위치를
지속적으로 부여한다. 이 구현에는 안정적인 Paxos leader나 NativeLoglet 내부 leader
election이 없다.

## 의도적으로 제외한 범위

- MetaStore Paxos의 온라인 membership 변경과 joint consensus
- 안정적인 Paxos leader와 Multi-Paxos 최적화
- background compaction과 외부 backup
- 운영 failure detector, 인증, 권한 관리와 TLS
- Controller 기반 network fault injection, scripted scenario와 event timeline
- Docker/Compose 배포

따라서 이 프로젝트의 “Paxos MetaStore”, “NativeLoglet”, “VirtualLog”이라는 이름은 위
계약 범위를 뜻하며 Delos 논문의 전체 구현 완료를 뜻하지 않는다.

## License

[MIT License](LICENSE). Copyright (c) 2026 Sunghyun Kim.

기여 절차는 [CONTRIBUTING.md](CONTRIBUTING.md), 보안과 배포 경계는
[SECURITY.md](SECURITY.md)를 참고한다.
