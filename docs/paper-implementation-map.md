# Delos 논문과 이 구현의 경계

기준: [Virtual Consensus in Delos, OSDI 2020](https://www.usenix.org/system/files/osdi20-balakrishnan.pdf).
“제외”는 미완성을 숨기는 표현이 아니라 이 학습 구현이 주장하지 않는 계약이다.

| 논문 개념 또는 계약 | 이 저장소 | 판정 |
|---|---|---|
| VirtualLog의 선형화 가능한 단일 가상 주소 공간 | 반개구간 LogChain, 위치 변환, active append | 구현 |
| Loglet 설정은 VirtualLog에 opaque | `kind/version/parameters`, Native adapter에서만 해석 | 구현 |
| `append`, `(tail, sealed) checkTail`, 범위 `readNext`, `seal` | generic Protocol과 Native adapter | 구현 |
| sparse Loglet 주소 공간 | generic `readNext`와 sparse adapter 테스트 | 구현 |
| `prefixTrim` | Loglet/VirtualLog `prefix_trim`; Native quorum trim watermark와 SQLite 영속화 | 구현 |
| `reconfigExtend` | `VirtualLog.reconfig_extend` | 구현 |
| `reconfigModify`, `reconfigTruncate` | sealed segment opaque 설정 교체, Native replacement 준비/검증, 물리 trim 후 첫 segment CAS 제거 | 구현 |
| seal -> checkTail -> conditional MetaStore write | 동일 순서, CAS 경쟁 테스트 | 구현 |
| 실패한 재구성을 임의 클라이언트가 roll-forward | VirtualLog이 timeout 후 이전 active의 opaque 설정을 새 segment로 clone; CAS 패자는 승자 체인 채택 | 구현 |
| 버전된 단일 레지스터 MetaStore | `virtual_log.metastore.MetaStore` | 구현 |
| embedded MetaStore의 독립 single-slot Paxos 연속열 | 슬롯별 Classic Paxos와 barrier read | 구현 |
| 각 Paxos 인스턴스가 다음 membership 저장 | 고정 membership | 제외 |
| NativeLoglet의 고정 sequencer와 LogServer 과반 append | 동일 | 구현 |
| append가 성공 또는 seal까지 내부 재시도 | registry 소유 pending 작업이 동일 command/position을 정족수 성공 또는 seal까지 재시도 | 구현 |
| `knownTail` 전파와 dense global prefix | 메시지 piggyback, 선행 위치 검사 | 구현 |
| none-sealed checkTail의 notification API | LogServer local-tail-or-seal notification을 기다리며 `knownTail` 또는 저장 과반 증거로 확정 | 구현 |
| all-sealed checkTail의 max-tail repair | conflict 검출과 seal 우회 repair | 구현 |
| local-first `readNext`, 복사본 하나면 충분 | local-first 탐색; 빈 위치는 모든 멤버 응답 전에는 확정하지 않음 | 구현 |
| converged 또는 disaggregated NativeLoglet | 동적으로 추가 가능한 converged DB 프로세스만 제공 | 부분 구현 |
| StripedLoglet, ZKLoglet, BackupLoglet, LDLoglet | 없음; non-Native test adapter만 존재 | 제외 |
| RocksDB 기반 ACID Table DB, strict serializability | SQLite 단일-key 교육용 KV/SMR | 축소 구현 |
| DB snapshot과 state transfer | KV·dedup·적용 위치의 원자적 snapshot과 VirtualLog replay bootstrap | 축소 구현 |
| 새 DB/LogServer 편입 | application catch-up 후 새 NativeLoglet segment에 storage member 추가 | 구현 |
| compaction과 외부 backup | 없음; VirtualLog `prefixTrim`만 구현 | 제외 |
| 운영 failure detector와 container manager signal | 요청 경로 오류 감지와 수동 프로세스 정지 | 축소 구현 |

## 이 구현만의 실험 장치

- Lab Controller는 Python subprocess를 시작·종료하고 HTTP 상태를 읽어 투영한다.
- Controller는 프로토콜 메시지를 가로채거나 LogChain을 결정하지 않는다.
- UI의 “latest observed chain”은 관측된 MetaStore state machine 중 최신 버전이지 별도
  consensus authority가 아니다.
- “Observed physical entries”는 LogServer에서 읽힌 복사본이다. 존재만 보여 주며,
  누락을 hole로 또는 active segment의 모든 물리 엔트리를 committed로 판정하지 않는다.
- Docker, transport fault injection, scripted scenario, timeline은 없다.

## 형식 검증 범위

`formal/VirtualLogReconfiguration.tla`는 경쟁하는 `reconfigExtend`, `prefixTrim`,
`reconfigTruncate`, sealed `reconfigModify`에서 다음 불변식을 검사한다.

- 세그먼트 범위에 gap이나 overlap이 없다.
- 닫힌 경계와 다음 시작점이 같은 first-unwritten position이다.
- 체인 버전과 경계가 단조 증가한다.
- configuration 수와 segment 수가 일치하며 modify는 주소 범위를 바꾸지 않는다.
- truncate는 trim 증거가 있는 선두 sealed segment만 제거한다.

`formal/NativeLoglet.tla`는 단일 sequencer 위치, append 정족수, seal, partial/zombie append,
sealed repair와 trim을 모델링한다. Paxos 전체의 형식 모델은 여전히 제외하며, Paxos는 단위,
property, process integration 테스트로 검증한다.
