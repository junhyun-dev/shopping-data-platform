# 실행 예와 기술 설명

[프로젝트 소개와 기본 실행](../README.md) 다음에 필요한 내용을 골라 읽는 문서입니다. 아래 두 화면 예제는 기본 DB와 다른 폴더에 결과를 만듭니다. 이미 준비한 폴더가 있으면 다시 적재하지 말고 `serve`로 엽니다.

<a id="comparison-demo"></a>
## 두 계산 결과 비교하기

기본 설치와 UCI 파일 준비를 마친 저장소 루트에서 실행합니다. 첫 실행은 상품 `35004C`의 수량을 `5`, 두 번째는 `53`으로 만듭니다. [README의 세 화면](../README.md#interactive-demo)이 이 예제입니다. 이미 저장한 두 결과를 현재 앱으로 열어 캡처했으며, 화면의 문구나 수치를 이미지 편집으로 바꾸지 않았습니다.

```bash
(
  set -eu
  demo_root="var/interactive-comparison"
  if test -e "$demo_root"; then
    printf '%s\n' "$demo_root already exists; reopen it with serve instead of preparing it again" >&2
    exit 1
  fi
  python -m shopping_data run \
    --database "$demo_root/shopping.duckdb" \
    --published-root "$demo_root/published" \
    --rows 143,156,202
  python -m shopping_data run \
    --database "$demo_root/shopping.duckdb" \
    --published-root "$demo_root/published" \
    --rows 143,156,202,299
)
```

```bash
python -m shopping_data serve \
  --database var/interactive-comparison/shopping.duckdb \
  --published-root var/interactive-comparison/published \
  --port 8766
```

<http://127.0.0.1:8766>에서 `35004C`의 근거를 엽니다. 전체 수량 `53`은 C 표시가 없는 두 행의 `54`와 C 표시가 있는 한 행의 `-1`로 구성됩니다. 표시별 버튼을 누르면 그 행들만 읽을 수 있습니다.

처리 이력에서 두 결과를 비교하면 Excel 299행의 `+48`이 추가된 것을 볼 수 있습니다. 이전 결과에는 156행 `-1`과 202행 `+6`만 있습니다. 각 행의 **선택한 두 결과에서 이 행 보기**로 그 행이 어느 결과에 기여했는지 확인하고, **이전 근거**에서 당시 SQL을 연 뒤 비교로 돌아옵니다.

서버를 종료했다가 같은 `serve` 명령으로 다시 열어도 두 결과가 남습니다. 상품이 한쪽 결과에 없으면 `0`으로 바꾸지 않고 없다고 표시합니다. 저장 결과를 읽는 데 실패한 경우도 행이 없는 경우와 구분합니다.

<a id="date-demo"></a>
## 날짜 선택과 주소 재열기

다음 세 행은 상품 `22752`의 날짜별 수량을 만듭니다. 12월 1일은 `+2` 한 행, 12월 2일은 `+12`와 `-2` 두 행입니다.

```bash
(
  set -eu
  demo_root="var/date-reopen-demo"
  if test -e "$demo_root"; then
    printf '%s\n' "$demo_root already exists; use serve to reopen" >&2
    exit 1
  fi
  python -m shopping_data run \
    --database "$demo_root/shopping.duckdb" \
    --published-root "$demo_root/published" \
    --rows 1361,3337,3340
)
python -m shopping_data serve \
  --database var/date-reopen-demo/shopping.duckdb \
  --published-root var/date-reopen-demo/published \
  --port 8767
```

<http://127.0.0.1:8767>에서 `22752`를 검색하고 시작·종료 날짜를 모두 `2010-12-02`로 입력합니다. 수량 `10`의 근거를 열면 Excel 3337행과 3340행, 당시 SQL을 볼 수 있습니다. 주소를 복사해 새 탭에서 열거나 새로고침해 같은 결과가 나오는지 확인합니다. 서버를 종료했다면 위 `serve` 명령으로 다시 엽니다.

기간은 양 끝 날짜를 포함합니다. 한쪽 날짜만 입력하면 그쪽부터 또는 그쪽까지 보고, 둘 다 비우면 전체를 봅니다. `2010-12-03`만 고르면 일치하는 결과가 없습니다. 시작일이 종료일보다 늦으면 입력 오류로 표시합니다. 이 필터는 저장한 날짜별 결과를 골라 보여 주며 수량을 다른 날짜로 옮기지 않습니다.

주소에는 실행 ID, 상품 검색어, 시작·종료 날짜, 정렬 순서가 들어갑니다. 특정 숫자를 선택했다면 정확한 상품 코드와 날짜도 들어갑니다. 검색어는 128자까지이며 제어 문자는 허용하지 않습니다. 비교쌍 전체·탭·스크롤 위치는 저장하지 않습니다. 고정 주소의 상세가 존재하고 화면이 한 열이면 상세가 보이는 위치로 이동합니다.

과거 근거를 잠시 읽는 동안은 원래 주소와 결과를 유지합니다. 최신 결과 조회에 실패하면 보던 결과가 남습니다. 주소의 실행이 없거나 결과 파일이 손상됐을 때는 다른 실행으로 자동 이동하지 않습니다.

<a id="data-meaning"></a>
## 수량과 원천 취소 표시

집계 기준은 실행에서 선택한 행의 `StockCode`와 `InvoiceDate`의 날짜 부분입니다. `Quantity`의 부호 있는 합계와 행 수를 따로 계산합니다. 상품 설명이 다른 행도 이 두 기준이 같으면 같은 묶음에 들어가며, 설명 원문은 기여 행에서 읽을 수 있습니다.

수량은 정수로 손실 없이 변환할 수 있어야 합니다. 소수 수량이나 범위를 벗어난 단일 수량은 실패로 처리합니다. 큰 합계는 JSON에 정확한 정수 문자열도 기록하고 브라우저에서 그 문자열을 사용합니다. 과거 결과에 정밀 문자열이 없고 숫자가 JavaScript의 안전 정수 범위를 넘으면 정확한 값을 알 수 없다고 표시합니다. 단가는 원본에서 읽은 문자열로 보여 주며 금액을 계산하지 않습니다.

[UCI 설명](https://archive.ics.uci.edu/dataset/352/online+retail)에 따르면 `InvoiceNo`의 `C` 접두는 취소를 나타냅니다. [변환 SQL](../sql/typed_transaction_lines.sql)은 숫자형·문자형 청구번호에서 앞뒤 ASCII 공백·탭·CR·LF를 제외하고 C/c 접두를 읽습니다. 없거나 지원하지 않는 셀 타입이면 `unknown`입니다. `false`도 판매 확정을 뜻하지 않습니다.

`D / Discount` 같은 기록의 업무 분류와 취소의 원판매 연결은 확인되지 않았습니다. 같은 상품과 단가의 앞선 행이 여러 개 있어 가장 가까운 행이라고 연결할 수는 없습니다. 다음 명령은 세 행을 임시 DB에서 관찰하고 독립 Python 계산과 비교한 뒤 JSON만 출력합니다. 기본 DB와 화면은 바꾸지 않습니다.

```bash
python -m shopping_data observe-source-meaning
pytest -q tests/test_source_meaning.py
```

예를 들어 앞선 날짜의 `+2`와 다음 날의 `+12, -2`를 원문 날짜대로 읽으면 `2, 10`입니다. `-2`를 앞선 날짜로 옮긴다고 가정하면 `0, 12`가 되지만, 이는 영향 비교일 뿐 채택한 원거래 연결 규칙이 아닙니다.

<a id="recovery"></a>
## 처리 중단 후 복구

DB별 파일 잠금(`flock`)을 얻은 프로세스 하나만 쓰기를 수행합니다. 다음 writer는 잠금을 얻은 뒤 DB에 남은 실행 기록을 확인합니다. 실행 시간이 오래 지났다는 이유만으로 살아 있는 프로세스를 실패로 처리하지 않습니다.

- 상세 트랜잭션이 끝나기 전에 종료되면 새 상세와 실행별 행 연결은 rollback됩니다.
- 상세 저장 후 결과 게시 전에 종료되면 마지막 정상 결과를 유지하고 중단 이력을 기록합니다. 미게시 JSON은 해당 실행 ID의 파일만 `orphaned/`로 옮깁니다.
- 성공 게시 후 응답을 보내기 전에 종료되면 이미 성공한 결과를 실패로 되돌리지 않습니다.

[프로세스 종료 테스트](../tests/test_pipeline.py)는 자식의 준비 신호를 받은 후 해당 PID를 `SIGKILL`하고 DB와 결과 파일을 확인합니다. 원본 위치가 같은 행을 다시 처리해도 저장된 원본 수와 합계가 늘지 않는지도 검사합니다. 같은 내용이라도 원본 위치가 다른 두 행을 업무 중복으로 보고 지우지는 않습니다.

이 검사는 로컬 프로세스 종료에 대한 것입니다. 전원·디스크·파일시스템 손실의 내구성이나 writer 실행 중 별도 프로세스의 지속적인 조회 가능성을 검증하지는 않았습니다.

<a id="loading"></a>
## 적재 방식과 메모리 사용

처음 1,000행을 처리할 때 상품·날짜 묶음 590개마다 기여 행 SQL을 호출했습니다. 이를 실행 전체의 기여 행을 한 번 읽는 방식으로 바꾸고, 기존 결과와 행 내용·순서가 같은지 비교했습니다. 각 묶음의 수량·행 수·원본 위치가 집계와 맞지 않으면 게시 전에 실패합니다.

그다음 계측에서는 전체 약 38초 중 약 37초가 원본 삽입, 청구번호 종류 보완, 실행별 행 연결의 세 `executemany` 호출에 들어갔습니다. 열 배열을 넘기는 대안은 약 20.5초였지만 입력 변환에 대부분의 시간을 썼습니다. JSON 문자열 하나를 임시 테이블로 읽는 방식은 작은 표본에서 더 빨랐습니다.

첫 JSON 구현의 10,000행 실행은 게시 전 signal 9로 종료됐고 최대 RSS는 `9,582,880 KiB`였습니다. 같은 시각대에 kernel OOM 기록이 있었지만 PID를 직접 연결한 기록이 없어 같은 프로세스라고 확정하지 않았습니다. 중단 DB에서는 이전 정상 결과가 남았고 실패 실행의 행 연결은 저장되지 않았습니다.

메모리를 제한한 작은 진단에서 JSON 문서를 상관 전개하던 경로가 크게 늘어나는 것을 확인했습니다. 현재 코드는 배열을 `pipeline_source_items`에 한 번 펼친 뒤 객체·정확한 키·타입·행 수·원본 위치를 검사합니다. `from_json_strict`만 사용하면 누락 필드가 NULL이 될 수 있어 별도 형태 검사를 둡니다. 이후 같은 트랜잭션에서 원본 첫 값을 보존하고, NULL인 청구번호 종류만 보완하며, 이번 실행의 행을 연결합니다. 구형 DB의 열순서가 달라도 맞도록 INSERT 대상 열을 명시했습니다.

수정 후 실제 10,000행의 4,553개 묶음과 10,000개 기여 행이 독립 openpyxl 계산과 일치했습니다. 해당 실행의 관찰 시간은 약 `1.62초`, 프로세스 최대 RSS는 `275,688 KiB`였습니다. 주소공간 `2 GiB`, DuckDB 메모리 `512 MiB`, thread 1로 제한한 로컬 자식에서 한 번 측정한 값입니다. 앞선 1,000·2,000행은 다른 자식에서 같은 DB에 처리했습니다. 캐시와 실행 순서를 통제한 성능 비교나 운영 SLA로 해석할 수 없으며, 이 자원 제한이 기본 실행에 자동 적용되지는 않습니다.

구현 참고: DuckDB의 [Python 대량 삽입 안내](https://duckdb.org/docs/lts/clients/python/dbapi)와 [JSON 변환 함수](https://duckdb.org/docs/lts/data/json/json_functions).

<a id="kafka"></a>
## Kafka 재전달 실험 실행하기

Java 17과 기본 Python 실행 환경이 필요합니다. 도구는 [설정](../config/kafka-runtime.json)에 지정한 Apache Kafka 4.1.2 공식 배포 파일과 SHA-512를 확인하고 `var/kafka/`에 저장합니다. 단일 KRaft broker, heap 256 MiB, loopback `19092/19093`, partition 1개·복제 1개를 사용합니다.

```bash
python -m pip install "confluent-kafka==2.3.0"
kafka_run_root="$(mktemp -d -p /tmp shopping-kafka-runtime-XXXXXXXX)"
python tools/run_kafka_experiment.py --run-root "$kafka_run_root"
```

[이벤트](../config/kafka-events.jsonl)와 [전달 순서](../config/kafka-deliveries.jsonl)는 합성 자료입니다. 같은 이벤트 ID와 내용이 다시 오면 합계에 더하지 않고, 같은 ID의 내용이 달라지면 충돌로 멈춥니다. 이벤트와 Kafka의 전달 위치는 다른 식별자로 보관합니다.

저장소에는 Kafka cluster·group·topic·partition과 다음에 읽을 offset을 이벤트와 같은 트랜잭션으로 기록합니다. Kafka의 읽은 위치만 앞서 있고 저장소가 비었거나 서로 다른 작업의 저장소를 연결하면 자동 reset 없이 거부합니다. 결과 저장 후 offset commit 전 종료, offset commit 후 로컬 처리 이력 기록 전 종료를 각각 재현합니다.

최종 수량은 독립 Python·SQL 배치와 비교합니다. 합성 `W-104`는 9월 21일 `4`, 9월 22일 `5`, `B-208`은 9월 22일 `-1`입니다. 늦게 도착한 이벤트도 UTC 발생일로 계산합니다. 이 규칙은 UCI 거래 날짜 정책과 별개입니다.

포트가 사용 중이면 다른 프로세스를 종료하지 않고 실패합니다. 도구는 새거나 빈 실험 디렉터리만 사용하고 자신이 시작한 프로세스를 종료합니다. 결과는 그 디렉터리의 `experiment-result.json`, `sink.duckdb`, `broker.log`에 남습니다.

이 실험은 Kafka가 재전달할 수 있는 상황에서 저장소가 중복을 처리하는 방식입니다. Kafka offset과 외부 DuckDB를 하나의 원자적 트랜잭션으로 묶지 않습니다. 여러 consumer의 rebalance, broker 장애, 전원·디스크 손실, 운영 부하는 검사하지 않았습니다. [실험 코드](../tools/run_kafka_experiment.py)와 [테스트](../tests/test_kafka_lab.py)에서 구체 조건을 읽을 수 있습니다.
