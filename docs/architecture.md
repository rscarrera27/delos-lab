# 아키텍처

Delos Lab은 프로토콜 계약, 구체 구현, 프로세스 조립, 실험 제어를 서로 다른 계층으로
분리한다. Converged 배치는 여러 컴포넌트를 같은 장애 단위에 놓는 실행 결정이지, 그
컴포넌트의 코드 소유권을 합치는 결정이 아니다.

## 논리 계층

```text
KV state machine
      │ append / read global positions
      ▼
VirtualLog ───── versioned read/CAS ─────> MetaStore port
      │                                      ▲
      │ opaque LogletConfiguration           │
      ▼                                      └─ Paxos implementation
VirtualLoglet port
      ▲
      └─ NativeLoglet adapter and implementation
```

- `virtual_log`는 가상 주소 공간, LogChain, 공통 Loglet 포트와 MetaStore 포트를 소유한다.
- `native_loglet`은 Native 설정 해석, sequencer, LogServer, 정족수와 adapter를 소유한다.
- `metastore.paxos`는 VirtualLog이 정의한 버전 레지스터 포트를 구현한다.
- `kv`는 공통 VirtualLog API를 사용하는 상태 머신과 snapshot 계약을 소유한다.
- `runtime`은 위 구체 구현을 실행 가능한 프로세스로 조립한다.
- `controller`는 subprocess 수명주기와 관찰·프록시만 담당한다.
- `frontend`는 Controller가 제공한 관측 투영만 소비한다.

`tests/unit/test_dependency_boundaries.py`가 다음 규칙을 코드로 고정한다.

- Delos 코어 패키지는 `controller`를 import하지 않는다.
- 프로토콜 패키지는 실행 조립 계층인 `runtime`을 import하지 않는다.
- `virtual_log`는 NativeLoglet이나 MetaStore 구현을 import하지 않는다.
- `controller`는 VirtualLog, NativeLoglet, Paxos 또는 KV 구현을 import하지 않는다.
- KV 서비스는 NativeLoglet이 아니라 공통 Loglet 계약에 의존한다.

## 런타임 토폴로지

```text
Browser
  └─ Controller :9400
       ├─ SPA와 /api 제공
       ├─ MetaStore process meta-1
       ├─ MetaStore process meta-2
       ├─ MetaStore process meta-3
       ├─ Converged Database process db-xxxxx
       ├─ Converged Database process db-xxxxx
       └─ Converged Database process db-xxxxx
```

MetaStore 프로세스는 고정 Paxos peer다. 각 Database 프로세스는 다음 컴포넌트를 같은 OS
프로세스에 배치한다.

- KV Application과 materializer
- VirtualLog client
- NativeLoglet client
- LogServer
- 해당 segment 설정이 지목할 때 활성화되는 sequencer

따라서 Database 프로세스를 Pause하거나 Kill하면 이 컴포넌트가 함께 실패한다. 반면
MetaStore 프로세스는 별도 장애 단위다. Controller 자신은 LogChain이나 Paxos 결정을
내리지 않는다.

## 요청 경로

변경 요청의 정상 경로는 다음과 같다.

1. Controller가 선택한 Database 프로세스의 KV API로 요청을 프록시한다.
2. KV 서비스가 명령 envelope를 `VirtualLog.append`에 전달한다.
3. VirtualLog가 활성 segment의 opaque 설정과 일치하는 adapter를 고른다.
4. NativeLoglet sequencer가 위치를 할당하고 LogServer 과반에 복제한다.
5. VirtualLog가 로컬 위치를 전역 가상 위치로 변환한다.
6. materializer가 VirtualLog를 순서대로 재생하고 KV 상태와 요청 중복 제거 결과를 같은
   SQLite 트랜잭션에 반영한다.

GET은 새 로그 엔트리를 만들지 않는다. `checkTail`로 확인할 수 있는 위치까지 catch-up한
뒤 로컬 상태를 읽는다. Controller polling은 `/state`와 관찰용 엔트리 조회만 수행하므로
Database 상태를 전진시키지 않는다.

## 재구성 경로

활성 sequencer가 불가용하면 요청을 받은 Database 프로세스의 VirtualLog 경로가 다음을
수행한다.

```text
seal(active)
  → checkTail(active)
  → old segment의 virtual_stop 확정
  → 새 NativeLoglet 설정 준비
  → MetaStore.compareAndSet
  → 원래 명령 재시도
```

이 과정은 NativeLoglet 내부 leader election이 아니다. 새 sequencer는 새 segment의
설정에 기록된다. CAS 경쟁의 패자는 승자의 LogChain을 다시 읽는다.

## Database 프로세스 추가

새 Database 프로세스는 빈 상태로 저장 정족수에 바로 들어가지 않는다.

1. 기존 Database에서 KV 값, dedup 결과와 적용 위치의 원자적 snapshot을 받는다.
2. snapshot 이후 VirtualLog suffix를 현재 tail까지 재생한다.
3. 준비가 끝난 뒤 `reconfigExtend`로 새 NativeLoglet segment를 설치한다.
4. 새 segment부터 새 프로세스가 LogServer이자 sequencer 후보가 된다.

Controller manifest의 endpoint 목록은 전송 주소를 해석하기 위한 것이며 protocol
membership의 권위가 아니다. 권위 있는 storage membership은 segment의 NativeLoglet
설정에 있다.

## 상태와 관찰의 의미

| 관측값 | 의미 | 의미하지 않는 것 |
|---|---|---|
| `running` | Controller가 시작한 subprocess가 종료되지 않음 | 요청 성공, 정족수 가용성 |
| `reachable` | Controller가 typed state 응답을 읽음 | protocol readiness, 최신 상태 |
| LogServer local tail | 그 서버의 물리적 high-water mark | global commit prefix |
| `knownTail` | 해당 컴포넌트가 아는 global tail의 하한 | 즉시 실행한 `checkTail` 결과 |
| observed physical entry | 한 LogServer에서 읽힌 물리 복사본 | 그 위치의 commit 또는 hole 판정 |

UI는 이 값들을 권위 상태로 승격하지 않는다. 최신 LogChain 표시는 여러 MetaStore 관측 중
가장 높은 버전의 투영이며 별도 consensus 결과가 아니다.
