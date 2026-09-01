# Security

Delos Lab은 로컬 학습 환경이며 운영용 보안 경계를 제공하지 않는다.

## 지원 범위

- 기본 Controller bind 주소는 `127.0.0.1`이다.
- 인증, 권한 관리, 요청별 접근 제어와 TLS가 없다.
- Controller API는 프로세스를 Pause, Resume, Kill하고 runtime 데이터를 초기화할 수 있다.
- Database와 MetaStore의 내부 HTTP endpoint도 신뢰된 loopback 전송을 전제로 한다.

`--host 0.0.0.0`은 신뢰하는 사설 LAN 실험에서만 사용한다. 공용 Wi-Fi, 인터넷에 직접
연결된 host, port forwarding, public cloud ingress 또는 중요한 데이터가 있는 환경에
노출하지 않는다.

## 취약점 제보

민감한 악용 세부사항이나 실제 secret을 public issue에 올리지 않는다. 저장소의 GitHub
Security 탭에서 private vulnerability reporting을 사용할 수 있으면 그 경로를 우선한다.
비민감한 hardening 제안과 재현에 secret이 필요 없는 오류는 일반 issue로 제보할 수 있다.

이 저장소는 교육용이지만, protocol safety를 깨뜨리거나 의도한 loopback 경계를 우회하는
문제는 일반 기능 오류와 구분해 설명해 주는 것이 좋다.
