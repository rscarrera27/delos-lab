# Delos Lab 개념 지도

이 문서는 코드를 처음 읽는 학생을 위한 출발점이다. 기준 논문은 OSDI 2020의
[Virtual Consensus in Delos](https://www.usenix.org/conference/osdi20/presentation/balakrishnan)다.

## 1. 해결하려는 문제

일반적인 복제 시스템은 애플리케이션, 명령 순서 결정, leader election, membership 변경을
한 합의 프로토콜 안에 묶는다. 그러면 ordering protocol을 교체하기 어렵다. Delos는 공유
로그를 다음 두 층으로 나눈다.

```text
Application / state machine replication
                 │ shared-log API
                 ▼
VirtualLog control plane ── versioned CAS ──> MetaStore
                 │ opaque configuration          │
                 ▼                               └─ fault-tolerant consensus
       pluggable Loglet data plane
```

- `VirtualLog`는 여러 Loglet 주소 공간을 하나의 가상 주소 공간으로 연결하고 재구성한다.
- `Loglet`은 한 정적 구성 안에서 명령을 순서화하고 저장한다.
- `MetaStore`는 현재 LogChain을 보관하는 버전된 단일 레지스터다. 이곳에는 장애 허용
  합의가 필요하다.

핵심은 “합의를 없앴다”가 아니다. 반드시 장애 허용이어야 하는 합의를 저빈도 control
plane인 MetaStore에 한 번 구현하고, Loglet의 append 경로는 더 단순하게 만들었다.

### `replica`를 쓰는 범위

`replica`는 모든 분산 프로세스의 동의어가 아니다. 이 문서와 코드에서는 복제된 상태
머신의 논리적 사본을 말할 때만 사용한다. Delos DB 서버는 같은 명령 로그를 재생해 상태를
복제하므로 이 의미에서 database replica라고 부를 수 있다. 그러나 다음 대상은 더 정확한
이름을 사용한다.

| 대상 | 사용하는 이름 | 이유 |
|---|---|---|
| Controller가 시작·종료하는 항목 | node, process | 수명주기와 배치 대상을 말한다. |
| MetaStore 합의 구성원 | MetaStore node, Paxos peer | Paxos에서 수행하는 역할을 드러낸다. |
| NativeLoglet 저장 구성원 | LogServer | 논문이 정의한 프로토콜 역할이다. |
| 한 LogServer에서 읽힌 엔트리 | physical copy | 논리적 commit 여부와 구분한다. |

따라서 UI의 서비스별 `Process` 카드는 제어 가능한 OS 프로세스 목록이고, Database의
`NativeLoglet` 카드는 같은 DB 프로세스에 converged 배치된 sequencer와 LogServer 역할을
각각 투영한다. LogServers 표의 한 행은 LogServer 하나이며 활성 NativeLoglet의 로컬
상태만 보여 준다. 전체 Loglet과 과거 segment 탐색은 `Virtual Log` 화면의 책임이다. 이
대상들을 모두 “replica”라고 부르면 공동 장애 단위와 프로토콜 역할의 차이가 사라진다.

## 2. 공통 Loglet 계약

논문의 공통 API는 `append`, `checkTail`, `readNext`, `prefixTrim`, `seal`이다. VirtualLog은
이 다섯 계약을 모두 구현하고 `reconfigExtend`, `reconfigTruncate`, `reconfigModify`를
추가로 제공한다. Python 메서드는 각각 `prefix_trim`, `reconfig_extend`,
`reconfig_truncate`, `reconfig_modify`로 표기한다.

- `append(payload) -> position`: 성공한 명령의 Loglet 위치를 반환한다.
- `checkTail() -> (tail, sealed)`: `tail`은 첫 번째 unwritten position이다.
- `readNext(min, max)`: 범위에서 첫 번째 엔트리를 반환한다. 따라서 sparse Loglet도
  공통 인터페이스를 구현할 수 있다.
- `prefixTrim(p)`: `p`를 첫 보존 위치로 삼아 `[0, p)`를 제거한다.
- `seal()`: 호출 성공 뒤 시작된 append가 성공 응답을 받지 못하게 한다. 멱등이어야 한다.

`VirtualLog`의 모든 범위는 반개구간 `[start, stop)`이다. 예를 들어
`[0, 4) -> A`, `[4, ∞) -> B`이면 가상 위치 4는 B의 로컬 위치 0이다.

## 3. 재구성의 안전성

active Loglet을 바꾸는 순서는 다음과 같다.

```text
seal(active)
   │
   ▼
checkTail(active) = (t, true)
   │
   ▼
old.stop = old.start + t
new.start = old.stop
   │
   ▼
MetaStore.compareAndSet(oldVersion, candidateChain)
```

seal보다 먼저 새 체인을 게시하면 같은 가상 위치가 두 Loglet에 배정될 수 있다. CAS가
없으면 동시에 재구성한 두 클라이언트가 서로 다른 체인을 권위 있다고 생각할 수 있다.
늦은 쓰기나 sealed repair로 옛 Loglet에 `stop` 이후 엔트리가 생겨도 그 엔트리는 가상
주소 공간에 나타나지 않는다. 논문은 이를 zombie append라고 부른다.

재구성 클라이언트가 `seal` 후 MetaStore CAS 전에 종료될 수 있다. 다른 VirtualLog
클라이언트는 sealed active를 보면 MetaStore를 다시 읽고, 유예 시간 후에도 같은
체인이면 이전 active의 opaque Loglet 설정을 새 `segment_id`로 복제해 재구성을
roll-forward한다. 이 경로는 Native 설정 필드를 해석하지 않는다. 반면 open
Loglet이 불가용한 경우의 후속 설정 선택은 NativeLoglet 정책이다. 중단된 재구성을
완료하는 기능과 장애난 sequencer를 교체하는 정책을 같은 개념으로 부르면 안 된다.

NativeLoglet은 각 LogServer의 trim watermark를 영속화해 엔트리를 제거해도
`localTail`이 뒤로 가지 않게 한다. VirtualLog이 첫 sealed segment 전체를 trim하면
`reconfigTruncate`로 그 segment를 LogChain에서 제거한다. trim 후 체인의 첫
`virtual_start`는 0이 아닐 수 있다. `reconfigModify`는 active가 아닌 sealed
segment의 가상 범위를 유지하면서 opaque 설정만 교체한다. 교체 설정이 같은
엔트리를 제공한다는 점은 해당 Loglet 구현의 사전조건이다. NativeLoglet은 이 사전조건을
`NativeLogletReplacementPreparer`에서 수행한다. 기존 sealed range와 quorum-certified trim
watermark를 새 storage membership에 복사하고 seal/checkTail로 검증한 뒤에만 opaque
configuration update를 반환한다.

## 4. NativeLoglet

NativeLoglet은 Paxos가 아니다. 한 세그먼트의 고정된 sequencer가 위치를 부여하고,
LogServer 과반이 엔트리를 동기화하면 append가 globally committed 된다. sequencer가
죽으면 그 Loglet은 append에 불가용해지며, 내부 leader election 대신 VirtualLog가 기존
Loglet을 seal하고 새 sequencer를 가진 새 Loglet을 붙인다.

용어를 구분해야 한다.

| 용어 | 정확한 의미 |
|---|---|
| local tail | 한 LogServer가 관측한 로컬 위치의 high-water mark; 하위 모든 물리 복사본이나 commit을 보장하지 않음 |
| global tail | 모든 선행 위치가 globally committed인 가장 긴 prefix의 끝 |
| `knownTail` | 컴포넌트가 현재 알고 있는 global tail의 하한; 실제 global tail보다 뒤처질 수 있음 |
| VirtualLog tail | active Loglet의 tail을 해당 세그먼트 시작점만큼 이동한 가상 위치 |

UI는 sequencer, NativeLoglet client와 각 LogServer의 `knownTail`을 소유 주체별 지식
상태로 구분해 표시한다. 내부 관측값
`known_virtual_tail`은 active segment의 `virtualStart + knownTail`인 파생 하한이며,
실제 `checkTail` 결과처럼 보이지 않도록 replay lag 계산에만 사용한다.

한 LogServer는 이미 globally committed임을 `knownTail`로 아는 선행 엔트리를 저장하지
않을 수 있다. 그래서 `readNext`는 로컬 LogServer부터 찾되 필요하면 다른 LogServer에서
복사본을 찾는다. 반면 sealed `checkTail`은 서로 다른 local tail을 최대 위치까지 repair한
뒤 고정된 경계를 반환한다.

open Loglet에서 한 서버에만 더 긴 local tail이 관측되면 `checkTail`은 실패 횟수를 세어
포기하지 않는다. 각 LogServer의 local tail이 목표에 도달하거나 seal되는 notification을
기다린 뒤 5-state 판단을 다시 수행한다. Sequencer 역시 일시적인 비정족수에서 동일
position을 버리지 않고 quorum commit 또는 quorum seal을 관측할 때까지 재시도한다.

## 5. MetaStore와 Paxos

MetaStore는 `read`와 `compareAndSet(expectedVersion, newChain)`만 제공한다. 이 랩의
구현은 각 명령을 독립 Classic Paxos 슬롯에 결정한다. Prepare/Accept 정족수, ballot,
promise, accepted value, learned decision이 SQLite에 영속화된다.

Paxos proposer는 MetaStore 명령 하나를 슬롯에 결정하려는 요청 단위 역할이다. 지속적으로
로그 위치를 부여하는 NativeLoglet sequencer와 동일한 leader가 아니다. 이 구현에는
Multi-Paxos의 stable leader 최적화가 없으며 membership은 고정되어 있다.

## 6. 새 Database 노드의 두 단계 편입

새 Converged 노드는 시작과 동시에 NativeLoglet storage member가 되지 않는다. 빈 application
state를 가진 노드를 먼저 멤버로 넣으면 준비되지 않은 노드가 append quorum에 포함될 수
있기 때문이다.

```text
KV snapshot(P) 설치
        │ values + request dedup + applied position
        ▼
VirtualLog [P+1, tail) replay
        │
        ▼
Database replica 준비 완료
        │ reconfigExtend
        ▼
새 NativeLoglet segment의 LogServer membership
```

여기서 `replica`는 동일한 application state를 구체화한 사본이라는 뜻이다. LogServer
membership은 LogChain에 저장된 해당 NativeLoglet segment의 설정으로 따로 결정된다.
Controller manifest는 node ID를 HTTP endpoint로 해석할 뿐 protocol membership의 권위가
아니다. 새 노드는 과거 sealed segment의 복사본을 받을 필요가 없고, 새 segment의 로컬
위치 0부터 LogServer로 참여한다.

snapshot은 `kv_items`만 복사하지 않는다. 이미 성공한 client request가 새 노드에서 다시
적용되지 않도록 dedup 결과와 `applied_position`을 함께 전송한다. NativeLoglet 테이블은
application snapshot에 포함하지 않는다. catch-up에 필요한 suffix가 먼저 trim되면 아직
serving 전인 새 노드는 더 최신 snapshot으로 재기준화하고 replay를 다시 시도한다. 이
단계가 끝나지 않으면 NativeLoglet membership 변경도 실행하지 않는다.

## 7. Converged 배치와 코드 경계

DB 프로세스 하나에는 Application, VirtualLog client, NativeLoglet client, LogServer,
현재 구성에서 선택될 수 있는 sequencer가 함께 실행된다. 이것이 논문의 converged
배치이며 같은 프로세스 종료를 공동 장애로 경험한다.

그러나 배치가 같다고 코드 소유권도 합쳐지는 것은 아니다.

```text
virtual_log  <── native_loglet adapter
     ▲
     ├──── metastore implementations
     └──── kv service

runtime.converged ── 위 구현들을 한 프로세스에 조립
controller        ── subprocess 수명주기와 읽기 전용 관측
frontend          ── controller 투영 소비
```

`runtime.converged`만 구체 구현을 함께 안다. Controller와 UI는 append, seal, checkTail,
Paxos 결정에 참여하지 않는다. 프로세스 Resume/Pause/Kill은 실험 장치이고, 네트워크나
합의 프로토콜의 일부가 아니다.

Topology의 `running`은 subprocess가 종료되지 않았다는 Controller의 수명주기 사실이다.
`reachable`은 Controller가 해당 노드의 health와 typed state를 읽었다는 관측 사실이다.
둘 다 정족수 확보나 KV 요청 성공을 보장하지 않는다. UI는 이 둘을 프로토콜 카드에서
제외하고 서비스별 Process 테이블의 `Lifecycle`과 `Reachability` 열에만 표시한다.

## 8. 한 PUT을 따라 읽기

1. `kv.http_api`가 요청을 `KvCommandEnvelope`로 만든다.
2. `KvService`가 generic `VirtualLog.append`를 호출한다.
3. VirtualLog는 active segment의 opaque 설정에 맞는 adapter를 선택한다.
4. NativeLoglet sequencer가 로컬 위치를 정하고 LogServer 과반에 기록한다.
5. VirtualLog가 로컬 위치를 가상 위치로 번역한다.
6. `KvMaterializer`가 `readNext`로 엔트리를 재생하고 SQLite 상태 머신에 적용한다.

sequencer 프로세스를 멈추면 4가 실패한다. VirtualLog에 주입된 NativeLoglet
재구성 정책이 live incarnation을 골라 `seal -> checkTail -> MetaStore CAS`를 수행하고,
같은 명령을 새 active segment에 다시 append한다. KV 서비스는 이 과정을 조정하지
않고 VirtualLog 호출이 최종적으로 실패한 경우만 application 오류로 번역한다.

각 프로세스 시작은 새 incarnation ID를 만든다. 최신 segment가 지목한 sequencer node와
현재 node가 같더라도 incarnation이 다르면 그 프로세스는 해당 segment의 sequencer 요청을
거절한다. UI는 이를 LogServer의 `Role`로 부르지 않는다. `Sequencer` 열의
`Incarnation mismatch`는 이 fencing 상태를 뜻하며, 현재 프로세스 자체를 “stale”이라고
부르지 않는다.

다음은 [paper-implementation-map.md](paper-implementation-map.md)에서 논문과 랩의 차이를
확인하고, `src/delos_lab/runtime/converged_process.py`부터 조립 방향을 거꾸로 따라가면 된다.
