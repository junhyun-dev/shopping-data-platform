# Shopping Data Platform

결국 **실제 거래 표본의 상품별 숫자를 보고, 그 숫자에 들어간 Excel 원본 행과 계산 SQL까지 확인하는 로컬 서비스**다.

기본 실행은 UCI `Online Retail.xlsx`의 Excel 2–8행과 143행을 다룬다. 같은 보존 파일에서 실행별 선택 행을 바꿔 결과 차이를 확인할 수도 있다. `Quantity`의 부호를 그대로 보존하여 상품 코드와 원문 `InvoiceDate`의 날짜 부분으로 묶고 다음 두 값을 만들며, 상세에서는 `InvoiceNo`에 나타난 원천 취소 표시를 별도로 보여 준다.

- **표본 수량 합계:** 선택한 행들의 `Quantity` 합
- **관찰 행 수:** 그 상품·날짜 묶음에 들어간 선택 행의 수

이 결과는 하루 전체 판매·취소·환불·결제·순매출이 아니다. 특히 `D / -1`인 143행도 원문 그대로 보존할 뿐 업무 의미를 확정하지 않는다.

별도 Kafka 실험은 이 UCI 결과를 실시간화하지 않는다. 명시적인 소량 합성 이벤트만 사용해 **sink 저장 뒤 offset commit 전에 consumer가 종료되면 같은 이벤트가 다시 와도 합계가 불어나지 않는가**를 검증한다.

## 지금 실제로 이어지는 경로

```text
보존한 Excel bytes + source manifest
→ Python이 이번 실행에서 고른 행을 원값으로 적재
→ DuckDB가 타입과 InvoiceNo 원천 표시를 명시한 상세를 제공
→ 실행-원본 행 연결을 거쳐 독립 SQL이 상품·원문 날짜별 표본 합계를 계산
→ 실행별 JSON을 만든 뒤 성공한 결과만 current로 등록
→ 단일 writer 잠금과 DB 증거로 중단된 실행을 다음 실행에서 복구
→ loopback 화면에서 숫자 → 기여 행 → 원본 위치 / SQL 확인
→ 성공한 두 실행을 골라 값과 추가·빠진 원본 위치 비교
```

| 경계 | 현재 역할 |
|---|---|
| `source_files` | 파일 SHA-256, 출처와 보존 위치. 기존 `selected_excel_rows` 열은 첫 등록 당시의 호환용 스냅샷이며 실행 범위의 정본이 아님 |
| `source_rows` | 파일 ID·시트·Excel 행번호, 원래 셀 값과 InvoiceNo 셀 종류. 같은 위치의 기술 재적재만 막음 |
| `run_input_scopes` | 실행이 택한 시트·정렬된 Excel 행 집합, 그 집합의 지문, 적용한 집계 SQL 지문과 원천 변환 SQL 지문 |
| `run_input_rows` | 실행 ID와 이번 계산에 들어간 원본 위치의 연결. 집계·타입 검증·기여 행 조회가 이 연결에 한정됨 |
| `sql/typed_transaction_lines.sql` | 수량·시각 타입과 `InvoiceNo` 원천 취소 표시를 명시한 DuckDB view. 단가는 계산하지 않고 원본에서 읽은 문자열을 보존함. `CustomerID`는 결과 모델에 포함하지 않음 |
| `sql/sample_product_daily.sql` | 실행 ID로 선택 행을 한정한 뒤 상품 코드와 원문 시각의 날짜 부분별 표본 수량 합계·관찰 행 수 |
| `sql/source_cancellation_marker_breakdown.sql` | 상품·원문 날짜 결과를 C접두 관찰 `true / false / unknown`별 수량·행 수·기여 Excel 행으로 설명함. Description은 이 묶음 기준이 아님 |
| `sql/source_meaning_observation.sql` | 선택한 실행을 원문 날짜·상품 코드·설명·원천 취소 표시별로 나눠 수량 합계·행 수·기여 Excel 위치를 관찰함. 판매·취소 마트는 아님 |
| `pipeline_runs` | 성공·실패·중단 복구 실행과 writer 소유 흔적. 실패·미완료 실행은 마지막 정상 결과를 바꾸지 않음 |
| `published_results` | 성공한 실행의 JSON 중 화면이 읽을 현재 결과 한 개를 가리킴 |

파일 지문·시트·행번호는 **원본 위치 식별자**이지 주문·거래의 업무 중복키가 아니다. 내용이 같은 서로 다른 원본 행을 자동으로 지우지 않는다.

## 실행

Linux 또는 WSL2, Python 3.10 이상을 사용한다. 프로세스 잠금·중단 검증은 Linux `flock`과 `SIGKILL`에 의존한다. Windows 네이티브 실행은 지원 범위가 아니다.

```bash
git clone https://github.com/junhyun-dev/shopping-data-platform.git
cd shopping-data-platform
python3 -m venv .venv
. .venv/bin/activate
python -m pip install "duckdb==1.4.4" "openpyxl==3.1.5" "pytz==2025.2" "pytest>=9,<10>"
pytest -q tests/test_pipeline.py
```

데이터를 받기 전에는 가공 테스트 자료로 UCI 파이프라인의 20개 검사가 통과하고, 실제 UCI 파일을 읽는 1개 검사는 건너뛴다. 실제 자료로 실행하려면 [UCI 공식 다운로드](https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip)에서 ZIP을 받아 `Online Retail.xlsx`만 `data/source/Online Retail.xlsx`에 둔다. 원본 파일은 저장소에 포함하지 않는다.

```bash
mkdir -p data/source
# 내려받은 ZIP 안의 Online Retail.xlsx를 위 폴더에 둔 뒤 실행한다.
python -m shopping_data run
python tools/independent_check.py
pytest -q tests/test_pipeline.py
python -m shopping_data serve
```

파이프라인은 실행 전에 `config/source.json`의 SHA-256과 파일을 대조한다. 기대 지문은 `43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d`이며 다르면 중단한다. `independent_check.py`는 별도 openpyxl 계산값을 출력하고, 실제 UCI 테스트는 이와 같은 독립 계산을 SQL 결과와 대조한다. 파일을 받은 뒤에는 실제 원본 검사를 포함해 UCI 파이프라인의 21개 검사가 실행된다.

같은 파일에서 선택 범위만 바꾸는 실행 예는 다음과 같다. 행 순서는 범위의 뜻을 바꾸지 않으므로 `--rows 3,2`는 `[2,3]`으로 정규화된다.

```bash
python -m shopping_data run --rows 2,3
python -m shopping_data run --rows 3
python -m shopping_data run --rows 3,2
```

### 같은 음수 수량을 원천 표시별로 관찰하기

다음 명령은 보존 파일의 Excel `2·143·156`행을 **임시 DuckDB와 임시 결과 폴더**에서만 처리한다. 기본 `var/shopping.duckdb`, 기존 current, 공개 결과는 바꾸지 않는다.

```bash
python -m shopping_data observe-source-meaning
pytest -q tests/test_source_meaning.py
```

명령은 파일 SHA-256·시트·선택 행·격리 실행 ID와 범위 지문, 원천 변환 SQL 지문, 아래 관찰 SQL 지문과 실제 SQL을 함께 출력한다. DuckDB 결과는 같은 세 행을 openpyxl과 Python만으로 다시 계산한 결과와 대조한다.

| 원문 날짜 | 상품 코드 | 원문 설명 | C접두 관찰 | 부호 있는 Quantity 합계 / 행 수 | 기여 위치 |
|---|---|---|---|---:|---|
| 2010-12-01 | `85123A` | `WHITE HANGING HEART T-LIGHT HOLDER` | `false`—판매 확정 아님 | 6 / 1행 | Excel 2행 |
| 2010-12-01 | `D` | `Discount` | `true` | -1 / 1행 | Excel 143행 |
| 2010-12-01 | `35004C` | `SET OF 3 COLOURED  FLYING DUCKS` | `true` | -1 / 1행 | Excel 156행 |

두 `true / -1`행도 상품 코드와 설명이 다르므로 한 상품 수량으로 합치지 않는다. 이 결과가 말하는 것은 원문 `InvoiceNo`에 C접두가 보였다는 것과 해당 행의 부호 있는 수량뿐이다. `D`를 할인 조정으로 확정하거나 `35004C`를 원판매와 연결하지 않는다. 원판매 날짜로 다시 귀속하려면 검증된 원거래 연결, `D / Discount`의 업무 분류, 날짜 귀속 규칙이 더 필요하다. 값이 `0`인 관찰 묶음과 관찰 행 자체가 없는 상태도 합성 반례에서 따로 확인한다.

관찰 묶음에는 원문 `Description`도 들어가므로 상품·날짜·C접두 표시가 같아도 설명 원문이 다르면 별도 묶음으로 남는다. 이는 원문 차이를 찾기 위한 조사 단위이며, `Description`을 상품 식별키나 서비스 집계 기준으로 채택한 것이 아니다.

### 상품 결과에서 원천 표시별 기여 행 읽기

격리 관찰에서 확인한 차이를 실제 상품 상세에도 연결했다. 새로 만드는 결과는 상품·원문 날짜별 표본 수량 합계 아래에 `source_cancellation_marker=true / false / unknown`별 **부호 있는 수량 합계·관찰 행 수·기여 Excel 행**을 담는다. 별도 SQL과 `source_cancellation_marker_breakdown_rule_id`, 실제 적용 SQL을 결과 JSON에 함께 남긴다. 이 SQL은 `run_input_rows`로 이번 실행의 선택 행만 읽으며, 각 표시 묶음의 수량·행 수·기여 위치를 기존 상품 결과와 대사한 뒤에만 결과를 게시한다.

```bash
marker_runtime=$(mktemp -d -p /tmp shopping-marker-breakdown-XXXXXXXX)
python -m shopping_data run \
  --database "$marker_runtime/shopping.duckdb" \
  --published-root "$marker_runtime/published" \
  --rows 143,156,202
python -m shopping_data serve \
  --database "$marker_runtime/shopping.duckdb" \
  --published-root "$marker_runtime/published"
```

실제 UCI 세 행을 고른 격리 결과에서 `35004C`는 `표본 수량 합계 5 / 관찰 2행`이다. 상세를 열면 `표시 있음 / -1 / Excel 156행`과 `표시 없음 / +6 / Excel 202행`으로 나뉜다. `D / -1 / Excel 143행`은 별도 상품 코드 결과다. `5`는 두 부호 있는 원문 수량을 더한 표본 기준선이지 순판매가 아니며, `false`는 판매 확정, `true`는 원판매 연결·환불 완료를 뜻하지 않는다.

결과 JSON에는 실제로 관찰된 표시 묶음만 둔다. 예를 들어 위 `35004C`에 `unknown` 행은 없으므로 합계 `0`을 만들지 않고 화면에 `해당 행 없음`으로 표시한다. 합계가 `0`이어도 관찰 행이 있는 합성 반례와 구별한다. 표시별 합계와 각 기여 행 수량은 큰 정수도 브라우저에서 다시 더하거나 `Number`로 복원하지 않도록, lossless 검증을 마친 정수의 canonical 문자열을 별도 필드에 둔다. 이는 보존한 원문 문자열과 다른 표시용 표현이다. 과거 JSON에 이 문자열이 없으면 안전 정수만 기존 값으로 보여 주며, 안전 범위를 넘는 값은 정확한 값처럼 출력하지 않고 `정밀값 미기록/확인 불가`로 표시한다. Description이 다른 같은 상품 행도 상품·날짜·표시 묶음에는 함께 들어가되 각 기여 행의 원문 설명은 그대로 보존한다. 과거 JSON에 breakdown 자체가 없으면 화면은 `미계산/미확인`으로 읽고 기존 기여 행을 임의로 `0`이나 새 묶음으로 만들지 않는다.

<a id="interactive-demo"></a>
### 실제 `5 → 53` 비교를 처음 준비하고 다시 열기

다음 명령은 기본 `var/shopping.duckdb`와 분리된 `var/interactive-comparison/`에 실제 UCI 선택 범위 두 개를 **처음 한 번만** 만든다. 첫 실행은 `[143,156,202]`의 `35004C = 5 / 2행`, 두 번째 실행은 Excel 299행을 더한 `[143,156,202,299]`의 `35004C = 53 / 3행`을 게시한다. 기존 경로가 있으면 준비 명령은 건드리지 않고 거부한다. 준비 도중 실패했다면 완료된 데모로 간주하지 말고 오류를 확인한 뒤 다른 새 `var/` 하위 경로에서 다시 시작한다.

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

준비가 끝난 뒤에는 실행을 더 만들지 않고 같은 DB를 연다.

```bash
python -m shopping_data serve \
  --database var/interactive-comparison/shopping.duckdb \
  --published-root var/interactive-comparison/published \
  --port 8766
```

브라우저에서 `http://127.0.0.1:8766`을 열면 현재 공개 결과인 `53`부터 시작한다. 다음 한 흐름으로 확인한다.

1. 상품 코드 `35004C`를 찾아 `근거 보기`를 누른다. `표시 없음 54 / 2행`, `표시 있음 -1 / 1행`을 각각 눌러 Excel 202·299행과 156행을 확인한다.
2. Excel 299행의 `선택한 두 결과에서 이 행 보기`를 누른다. 기존 비교 쌍을 유지한 처리 이력에서 이 행은 이전 `5`에는 비기여, 이후 `53`에는 `+48` 기여로 나타난다. Excel 202행은 두 결과 모두 `+6` 기여다.
3. `처리 이력`에서 이전 `[143,156,202]`과 이후 `[143,156,202,299]`을 골라 `5 → 53`과 추가 Excel 299행을 확인한다.
4. 행 기여 카드나 비교표의 `이전 근거`에서 두 기여 행과 그 실행에 저장된 SQL을 읽고 `비교로 돌아가기`를 누른다.
5. 표본 결과에서 존재하지 않는 상품 코드를 검색해 `없음`이 `0`이 아님을 읽은 뒤 검색어를 지운다.
6. 터미널에서 `Ctrl-C`로 서버를 종료한다. 나중에 위 `serve` 명령만 다시 실행하면 저장된 두 결과와 읽기 전용 과거 근거가 그대로 열린다. 두 `run` 명령을 다시 실행하지 않는다.

화면은 자동 polling으로 보고 있던 실행을 바꾸지 않는다. 서버에 새 성공 결과가 게시된 뒤에는 상단의 `최신 결과 다시 읽기`를 눌러야 한다. 성공한 조회만 표시 결과를 바꾸며, 버튼 가까이에 **지금 표시 중인 실행**과 이 결과가 조회 당시 current였다는 사실을 보여 준다. 조회 실패나 조회 도중 다른 메뉴로 이동한 경우에는 지금 보던 결과와 화면을 유지한다. 과거 근거를 읽는 동안에도 임의로 최신 결과로 바꾸지 않는다. 따라서 “최신”은 버튼을 눌러 성공한 조회 시점의 뜻이지 상시 최신 보장이 아니다.

### 기존 실행 결과를 브라우저에서 비교하기

위 `observe-source-meaning` 명령은 JSON만 출력한 뒤 임시 DuckDB와 임시 결과를 제거하므로, 세 행 관찰 결과를 기존 화면에 게시하지 않는다. 아래 화면은 `python -m shopping_data run`으로 게시한 결과와 실행 이력을 `python -m shopping_data serve`로 읽는 별도 흐름이다.

브라우저에서 `http://127.0.0.1:8765`를 연다. `처리 이력`에서 성공한 두 실행을 고르면 상품·날짜별 이전/현재 값과 추가·빠진 Excel 행을 확인할 수 있다. 같은 파일·시트·집계 SQL·원천 변환 SQL에서 행 집합만 달라진 경우에만 **선택 범위 변경**으로 표시한다. 파일 지문이나 두 SQL 지문이 달라졌거나 과거 결과에 변환 지문이 없다면 그 기준을 따로 표시하며 늦은 입력이라고 추정하지 않는다.

화면은 비교 기준을 `같음 / 다름 / 일부 확인 불가`로 먼저 구분한다. 각 상품에서는 **수량 합계·관찰 행의 값 변화**와 **기여 원본 위치 변화**를 따로 읽고, 추가·빠진 원래 거래나 원본 파일·적용 SQL·원천 해석 규칙 가운데 다음에 확인할 근거를 연결한다. 현재 결과에 상품이 나타나지 않는 상태는 `0`이나 취소로 바꾸지 않는다. 값이 같아도 기여 위치나 규칙이 다르면 확인할 변화로 남고, 과거 결과에 규칙 ID가 없으면 같은 기준이었다고 단정하지 않는다.

비교 행의 `이전 근거`와 `이후 근거`는 그 실행이 성공할 때 저장한 JSON을 읽기 전용으로 연다. 화면은 어느 쪽 결과인지, 실행 ID·완료 시각·선택 범위를 먼저 표시하고 그 안의 상품 수량, 원천 취소 표시별 구성, 기여 Excel 행, 파일 지문·시트, 저장된 SQL을 기존 상세 패널에서 보여 준다. 비교의 한쪽에 상품이 없으면 `없음(0 아님)`으로 남기고 다른 상품을 대신 선택하지 않는다. `비교로 돌아가기` 전후에도 DB의 current와 비교 선택은 바뀌지 않는다.

상품 상세의 기여 행에서도 `선택한 두 결과에서 이 행 보기`로 같은 비교 쌍에 이동할 수 있다. 사용자가 고른 두 실행 ID가 새 처리 이력 목록에도 있으면 메뉴를 오가거나 목록을 다시 읽어도 그 쌍을 유지한다. 처음 열었거나 선택한 ID가 목록에서 사라진 경우에만 남아 있는 완료 결과로 다시 선택하고 그 사실을 비교 설명에 표시한다.

화면은 **파일 지문 + 시트 + Excel 행번호**가 모두 같은지를 각 저장 JSON의 `contributing_rows`에서 대조한다. 정상적으로 읽었지만 행이 없으면 `기여하지 않음`, 저장 결과를 읽지 못하면 `확인 불가`로 구별한다. 같은 행번호라도 파일이나 시트가 다르면 같은 원래 거래로 보지 않는다. 기여 행을 찾은 쪽은 그 저장 결과에서 실제로 행이 들어간 상품·날짜의 값과 `이 결과의 당시 근거`를 연결한다. 비기여 쪽은 원래 보던 상품·날짜 결과의 존재 여부를 보여 준다. 이는 저장 결과의 연결을 정직하게 읽기 위한 것이며 새로운 상품 변환 정책은 아니다.

실제 UCI 격리 실행 `[143,156,202] → [143,156,202,299]`에서 `35004C`는 `5 / 2행 → 53 / 3행`으로 바뀌었다. 이전 근거에는 Excel 156행 `-1`과 202행 `+6`, 이후 근거에는 같은 두 행과 Excel 299행 `+48`이 열린다. 이는 명시적으로 선택 범위를 넓힌 결과이며 신규 도착·정정·늦은 입력이나 순판매로 해석하지 않는다. 비교 수량도 결과 JSON의 canonical 정수 문자열을 우선 사용하므로 안전 정수 범위를 넘는 과거 값에 문자열이 없으면 정확한 값처럼 복원하지 않는다.

`/api/result`는 DB가 가리키는 current를 읽고, `/api/result?run_id=<실행 ID>`는 DB에 등록된 `succeeded` 게시 결과 하나만 읽는다. 요청 ID와 JSON의 실행 ID가 다르거나 파일이 없거나 손상됐거나 게시 루트 밖을 가리키면 current로 대체하지 않고 명시적으로 실패한다. 실패·처리 중 실행과 DB에 등록되지 않은 orphan 파일도 열지 않는다. 서버는 이 API와 `/api/runs`, `/api/results`, `/api/comparison`, `web/`의 명시된 세 파일만 제공하며 저장소·원본 폴더·상위 폴더를 웹으로 제공하지 않는다.

## 실제 프로세스 중단과 복구

파이프라인은 DB별 `<database>.writer.lock`을 `flock`으로 독점한 한 프로세스만 쓰게 한다. 다음 writer는 이 잠금을 얻은 뒤에만 DB에 `running`으로 남은 실행을 복구한다. 살아 있는 실행을 경과 시간만으로 실패 처리하지 않으며, 두 번째 writer는 `PipelineBusy`로 끝난다. 복구를 위해 별도 명령을 외우지 않아도 다음 정상 `python -m shopping_data run`이 먼저 중단 이력을 정리하고 새 실행을 시작한다.

고정된 sleep 대신 자식 프로세스가 준비 신호를 보낸 뒤, 테스트가 직접 소유한 그 PID만 `SIGKILL`하여 다음 네 경계를 확인한다.

| 종료 경계 | 재시작 뒤 확인하는 결과 |
|---|---|
| DB 상세 transaction commit 전 | 상세와 실행-행 연결은 rollback되고 이전 정상 결과가 current로 남는다. |
| 상세 commit 후, 결과 파일 생성 전 | 보존 상세는 남지만 공개 결과는 없고, 다음 writer가 실행을 `중단 복구` 실패로 기록한다. |
| 결과 파일 생성 후, current 게시 전 | 공개 목록에는 들어가지 않는다. 정확한 실행 ID의 파일만 `published/runs/orphaned/`로 옮겨 보존하고 실패 이력과 연결한다. |
| 성공 게시 commit 후, 호출자 반환 전 | 호출자가 응답을 잃어도 DB의 성공·current·결과 파일을 그대로 신뢰하며 실패로 되돌리지 않는다. |

복구 뒤 같은 입력을 다시 처리해도 원본과 합계는 증폭되지 않고, 이전 정상 결과와 이미 게시된 비교 후보도 남는다. 화면의 `처리 이력`은 복구된 실행을 `중단 복구`로 구별하고 현재 숫자는 마지막으로 게시된 정상 결과만 읽는다.

이 검증은 Python이 잡은 예외와 별도로 **로컬 처리 프로세스가 종료된 경우**를 다룬다. 현재 DuckDB 1.4.4에서는 살아 있는 별도 writer가 DB 파일을 잡는 동안 다른 프로세스의 read-only 연결도 실패할 수 있으므로, 처리 중에도 별도 UI 프로세스가 계속 읽을 수 있다고 약속하지 않는다. 프로세스 종료로 DuckDB와 `flock` 잠금이 풀린 뒤에는 마지막 정상 결과를 다시 읽고 복구할 수 있다. OS·전원 종료, 디스크 flush 손실, 파일시스템 고장까지의 내구성을 증명한 것은 아니다.

## 실제 Kafka 재전달 실험

기본 재전달·충돌·늦은 입력과 빈 sink의 진행 위치 불일치, UTC 시각, 큰 정수 합계 반례를 검증한 로컬 실험이다. 실제 broker에서 두 consumer 종료 경계를 재현하고, 빈 sink는 처리 성공으로 표시하지 않고 거부하는 것을 확인했다. 이 실험의 이벤트·날짜·중복 계약은 아래 합성 입력에 한정한다.

Kafka 실험의 원 이벤트는 [`config/kafka-events.jsonl`](config/kafka-events.jsonl), 전달 순서는 [`config/kafka-deliveries.jsonl`](config/kafka-deliveries.jsonl), 같은 ID·다른 내용 반례는 [`config/kafka-conflict-event.json`](config/kafka-conflict-event.json)이다. 원 이벤트의 `event_id`·내용 지문과 Kafka 전달 위치 `topic / partition / offset`을 분리한다.

- `event_id + 같은 내용 지문`: 재전달로 기록하고 `kafka_events`에는 한 번만 등록한다.
- `event_id + 다른 내용 지문`: 충돌로 기록하고 처리와 offset 전진을 중단한다.
- 이 규칙은 합성 이벤트 계약이다. UCI 청구·거래 중복 정책이 아니다.
- sink는 `cluster / group / topic / partition`과 다음에 처리할 offset을 한 번 결합한다. 이벤트 등록·전달 시도·다음 offset 갱신은 한 DuckDB transaction에 두고, Kafka offset은 그 transaction 성공 뒤 `commit(message, asynchronous=False)`로 별도 commit한다.
- 빈 sink인데 같은 group이 이미 앞서 있거나, broker가 sink보다 앞서 있거나, 로그 보존 시작점이 sink 뒤로 넘어간 경우에는 성공·자동 reset·자동 재구축을 추정하지 않고 중단한다. 반대로 sink 저장만 끝나 broker가 한 위치 뒤인 의도한 중단 창은 같은 위치의 정확한 이벤트를 재전달할 때만 허용한다.
- `occurred_at`은 초 단위 이상을 포함한 `Z` UTC 시각만 받고 날짜뿐인 값·timezone 없는 값은 거부한다. 개별 수량은 signed BIGINT 범위를 확인하되 `SUM`은 DuckDB의 더 넓은 정확한 정수 결과를 다시 BIGINT로 줄이지 않는다.

한 partition·한 consumer의 실제 흐름은 다음과 같다.

```text
Kafka offsets 0~3에 초기 네 번 전달
→ offset 0을 sink에 commit
→ offset commit 전 consumer PID만 SIGKILL
→ broker committed offset은 없음(-1001), sink에는 evt-1001 한 건 존재
→ 같은 group 재시작이 offset 0을 다시 읽어 redelivery 처리
→ offsets 0~3 처리 뒤 committed next offset=4
→ 같은 group에 새 빈 sink를 연결하면 broker=4 / sink 없음 불일치로 거부
→ 늦은 evt-1003을 offset 4에서 sink 저장하고 broker committed=5까지 마친 뒤, 로컬 audit 전 consumer PID를 다시 SIGKILL
→ 재시작은 broker=5 / sink next=5를 대조해 이벤트를 다시 받지 않고 중단 이력만 복구
→ evt-1002와 내용이 다른 반례를 offset 5에서 두 번 읽지만 committed=5 유지
```

최종 sink와 독립 Python·DuckDB SQL 배치는 모두 다음 합계와 일치했다.

| 발생일(UTC) | 상품 | 부호 있는 수량 합계 | 고유 이벤트 수 |
|---|---:|---:|---:|
| 2026-09-21 | `W-104` | 4 | 1 |
| 2026-09-22 | `B-208` | -1 | 1 |
| 2026-09-22 | `W-104` | 5 | 2 |

여기서 늦게 전달된 `evt-1003`은 도착한 날이 아니라 원 이벤트의 UTC 발생일 `2026-09-21` 결과를 새로 만든다. 초기 전달에서도 10:05 이벤트 다음에 09:40 이벤트를 넣어 발생시각 역순이 Kafka offset 순서와 다름을 보존했다.

실행은 Apache 공식 배포 파일인 `kafka_2.13-4.1.2.tgz`를 사용한다. [`config/kafka-runtime.json`](config/kafka-runtime.json)의 공식 SHA-512를 확인한 뒤 `var/kafka/`에만 보존·압축 해제한다. Java 17, 프로젝트 전용 단일 KRaft broker, 256 MiB heap, `127.0.0.1:19092/19093`, 한 partition·복제 1로 제한한다. Docker·공유 daemon·전역 설치는 사용하지 않는다.

```bash
python -m pip install "confluent-kafka==2.3.0"
kafka_run_root="$(mktemp -d -p /tmp shopping-kafka-runtime-XXXXXXXX)"
python tools/run_kafka_experiment.py --run-root "$kafka_run_root"
```

도구는 broker·controller port가 이미 사용 중이면 다른 프로세스를 종료하지 않고 실패한다. 새거나 빈 실험 디렉터리만 format하며 종료 시 자신이 시작한 broker와 consumer만 멈춘다. 성공하면 해당 디렉터리의 `experiment-result.json`, `sink.duckdb`, `broker.log`를 보존한다.

첫 실제 기동에서는 broker port와 metadata 응답이 준비된 직후에도 내부 consumer group coordinator가 아직 로딩 중이어서 `NOT_COORDINATOR`로 멈췄다. 실패한 실험 디렉터리를 지우지 않고 보존했으며, 현재 실행기는 metadata 확인과 별도로 coordinator가 committed offset 요청에 응답할 때까지 제한된 횟수로 기다린다. 응답의 partition별 오류도 진행 위치로 사용하지 않는다. 따라서 "broker에 연결됨"과 "consumer group을 복구할 준비가 됨"을 같은 상태로 취급하지 않는다.

Python client는 이미 설치된 `confluent-kafka 2.3.0 / librdkafka 2.3.0`을 사용했다. `enable.auto.commit=false`와 `enable.auto.offset.store=false`를 함께 고정한다. 공식 Python API가 설명하듯 `commit(message)`는 다음 offset을 동기 commit하고, `close()`는 auto commit이 꺼져 있으면 offset을 commit하지 않는다. Apache 프로토콜의 API-version 협상·librdkafka의 broker 호환 설명뿐 아니라 실제 4.1.2 연결, produce, classic consumer group, synchronous commit까지 직접 확인했다.

이것은 외부 DuckDB와 Kafka offset을 하나의 원자 transaction으로 묶은 exactly-once 보장이 아니다. **Kafka는 적어도 한 번 다시 전달할 수 있고, sink가 immutable event ID로 같은 내용을 멱등 처리해 최종 값을 맞춘 실험**이다. 단일 broker·복제 1이므로 broker 장애, 전원·디스크 손실, 여러 partition의 순서·rebalance, 운영 부하도 검증하지 않았다.

UCI의 공식 변수 설명은 `InvoiceNo`가 `C`로 시작하면 cancellation이라고 설명한다. 현재 구현은 [공식 데이터셋 설명](https://archive.ics.uci.edu/dataset/352/online+retail)에 근거해, 원문 `InvoiceNo`를 바꾸지 않고 관찰용 표시만 만든다.

- 숫자형 청구번호는 텍스트와 함께 `integer`, 문자형은 `text`라는 원본 셀 종류를 보존한다. 그 밖의 지원하지 않는 셀 타입은 원문 텍스트를 지우지 않되 표시를 `unknown`으로 둔다.
- 이 표시를 계산할 때만 앞뒤의 ASCII space·tab·CR·LF를 제거하고 첫 문자를 대소문자 구분 없이 `C`와 비교한다. 다른 Unicode 공백까지 제거한다고 약속하지 않는다.
- 원문이 없거나 지원 범위의 공백뿐이거나 셀 타입을 지원하지 않으면 `unknown`, C/c 접두가 보이면 `true`, 그 밖은 `false`다.
- `false`는 판매 확정이 아니다. 이 표시는 원판매 연결·환불·결제·할인 분류를 뜻하지 않는다.
- 과거 결과 JSON에 표시나 변환 지문이 없으면 화면은 이를 미계산/미확인으로 읽는다.

첫 실제 원본에서 확인할 대표 결과는 다음과 같다.

| 원문 날짜 | 상품 코드 | 표본 수량 합계 | 관찰 행 수 | 원천 취소 표시 | 기여 원본 |
|---|---:|---:|---:|---|---|
| 2010-12-01 | `85123A` | 6 | 1 | `false`—판매 확정 아님 | Excel 2행 |
| 2010-12-01 | `D` | -1 | 1 | `true`—원판매·환불 연결 미확인 | Excel 143행 |

`data 없음`, 결과 파일 `읽기 실패`, 실제 값 `0`은 서로 다른 상태로 취급한다. 상품 필터에 결과가 없으면 `0`이라고 표시하지 않는다.

### 상품 묶음이 늘어날 때 기여 행을 읽는 방법

상품·원문 날짜 결과마다 `contribution_rows.sql`을 다시 실행하면 결과 묶음 수만큼 같은 실행 범위를 반복해서 읽는다. 실제 Excel `2~1001`의 1,000행 격리 결과에는 상품·날짜 묶음이 590개였고, 기존 방식은 기여 행 SQL도 590번 실행했다. 현재 구현은 `run_input_rows`로 한정한 같은 1,000행을 SQL 한 번으로 읽어 `원문 날짜 + StockCode + 원본 행번호` 순서로 정렬한 뒤, Python에서 상품·날짜별로 묶는다.

이 변경은 집계 grain이나 JSON·API·화면 계약을 바꾸지 않는다. 각 묶음의 수량 합계·행 수·기여 위치는 기존 집계와 원천 취소 표시별 분해 결과에 다시 대사하고, 빠지거나 다른 실행의 행이 섞이면 게시 전에 실패한다. 원문 단가 문자열, C접두 관찰, 정수 수량 문자열과 파일·시트·행 정체성도 그대로 보존한다. SQL은 [`sql/contribution_rows.sql`](sql/contribution_rows.sql)에 남아 있다.

같은 격리 DB를 읽기 전용으로 네 번 교차 실행한 국소 관찰에서는 `590회 필터 조회`의 중앙값이 `6.026초`, `1회 조회 + Python 묶음`이 `0.0099초`였다. `EXPLAIN`에서도 전자는 상품·날짜 필터를 둔 조회를 묶음마다 실행하고, 후자는 실행 ID로 연결한 행을 한 번 읽어 날짜·상품·원본 행 순으로 정렬한다. OS·DB cache와 실행 순서를 완전히 통제한 benchmark가 아니며 전체 파이프라인 성능이나 운영 규모를 보장하지 않는다. 실제 1,000행 파이프라인 한 번은 `45.04초`였지만, 기존 단일 관찰 `46.37초`와의 차이를 이 조회 변경만의 효과로 해석하지 않는다.

새 1,000행 결과의 590개 `products`와 모든 기여 행은 변경 전 저장 JSON 및 별도 openpyxl 계산과 정확히 같았다. 한 실행당 기여행 SQL 1회, 다른 실행 비혼입, 대사 실패 시 last-good 보존, 큰 정수 정밀도는 관련 회귀검사로 따로 확인한다.

기여 행 조회를 한 번으로 줄인 뒤에도 전체 실행에서 무엇이 시간을 쓰는지 별도 격리 DB로 계측했다. 같은 실제 Excel `2~1001`을 처음 처리한 실행은 `38.063초`, 같은 파일·같은 범위를 다시 처리한 실행은 `38.734초`였다. 각 구간의 한 번 관찰은 다음과 같다.

| 구간 | 첫 실행 | 같은 범위 재처리 |
|---|---:|---:|
| 원본 SHA-256 | 0.016초 | 0.019초 |
| Excel 선택 행 읽기 | 0.168초 | 0.200초 |
| `source_rows` 행별 `INSERT OR IGNORE` | 23.129초 | 23.371초 |
| `invoice_no_source_kind` 행별 NULL 보완 | 7.200초 | 7.414초 |
| `run_input_rows` 행별 연결 | 7.180초 | 7.505초 |
| 타입·정밀도 검증 | 0.019초 | 0.013초 |
| 집계·표시별 구성·기여 행 결과 생성 | 0.036초 | 0.029초 |
| 결과 JSON 원자 작성 | 0.047초 | 0.035초 |

세 `executemany`만 합쳐 각각 `37.509초`, `38.289초`였다. 이미 같은 원본 1,000행이 저장된 재처리에서도 `INSERT OR IGNORE`와 NULL 보완 호출을 행별로 다시 보내므로 이 비용이 거의 그대로 반복됐다. 이 측정에서는 파일 hash·Excel 읽기·결과 SQL이 지배적이지 않았지만, 입력 크기·DB 상태·cache·실행 순서를 통제한 benchmark나 운영 SLA는 아니다.

production 코드를 바꾸지 않은 대안으로, 같은 transaction 안에 1,000행의 typed 임시 staging을 만들고 `source_rows` 보존 삽입·NULL kind 보완·실행 membership 연결을 각각 한 set-based 문장으로 실행했다. 첫 실행은 `20.512초`, 같은 범위 재처리는 `20.575초`였고 세 문장 자체는 합쳐 약 `0.02초`였다. 그러나 Python의 13개 열 배열을 DuckDB `UNNEST` 파라미터로 staging하는 데 각각 `20.023초`, `20.104초`가 들어 새 병목이 됐다. 결과의 590개 상품 묶음·1,000개 기여 행·규칙 지문은 현재 방식과 같고, 둘 다 기존 독립 openpyxl 결과와 일치했다.

대안에서도 파일·시트·행 위치의 `INSERT OR IGNORE`, 기존 kind가 NULL일 때만 채우는 `COALESCE`, 새 실행 ID의 정확한 membership을 유지했다. 작은 격리 반례에서 NULL kind는 staging 값으로 보완됐고, kind 보완과 membership을 commit 전에 rollback하면 영구 테이블은 원래 상태로 돌아왔다. 그래도 staging 전달이 20초를 쓰므로 이번 측정만으로 쓰기 계약을 교체하지 않는다. 다음 비교를 연다면 현재 의존성 안에서 Python 행을 DuckDB로 더 직접 전달하는 한 방법을 같은 transaction·NULL·멱등·실패 조건으로 검증해야 한다.

화면은 이 1,000개 선택 행을 상품표보다 먼저 전부 펼치지 않는다. 연속한 `2~1001`은 `Online Retail · Excel 2~1001행 · 1,000행 선택`으로 요약하고, `전체 행 펼치기`를 열면 정확한 행번호 전체를 다시 확인할 수 있다. `[143,156,202,299]`처럼 떨어진 짧은 범위는 `143~299`로 줄이지 않고 네 행을 각각 표시한다. 비연속 구간이 네 개를 넘어 요약에서 일부가 생략되면 선택 행이 12개 이하라도 전체 목록 펼치기를 제공한다. 같은 요약은 처리 이력의 실행 선택과 과거 결과 안내에도 사용한다. `2~1001 → 2~3` 비교는 **추가 행 없음 / 빠진 Excel 4~1001행 · 998행**으로 방향과 수를 먼저 보여 주고, 펼치면 빠진 998개 위치를 모두 확인할 수 있다. 저장 JSON의 `selected_excel_rows`나 계산 범위는 바꾸지 않는다.

상품 코드를 모를 때는 기존 저장 순서인 `기본 순서`를 유지하거나 `표본 수량 큰순 / 작은순`을 명시적으로 고를 수 있다. 정렬은 코드 검색과 함께 적용되고, 상품 상세·지난 실행 근거·비교를 다녀오거나 `최신 결과 다시 읽기`를 눌러도 현재 검색어와 보기 순서를 임의로 초기화하지 않는다. 이는 저장 결과나 current를 바꾸는 재계산이 아니라 이미 저장된 상품·원문 날짜 결과를 고르는 화면 동작이다.

실제 `2~1001` 격리 결과에서 큰순 첫 항목은 `17021 = 600 / 1행 / Excel 732행`이었다. 작은순은 `21983 = -24 / 1행 / Excel 239행`부터 보이며 `21984 = -24`도 같은 최소값이라 날짜·상품 코드 순으로 결정적으로 이어진다. 이 순서는 판매 순위가 아니라 **선택 표본의 부호 있는 Quantity 합계**를 살펴보는 입구다. 화면은 canonical 정수 문자열을 `BigInt`로 비교해 안전 정수 범위를 넘는 값도 손실 없이 정렬한다. 그 문자열이 없는 과거 결과는 안전 정수만 사용하며, 안전 범위를 넘는 미확인 값은 `0`으로 넣지 않고 순위를 확정하지 않은 채 정확한 값 뒤에 표시한다.

## 검증

```bash
pytest -q
python tools/run_kafka_experiment.py --run-root "$(mktemp -d -p /tmp shopping-kafka-runtime-XXXXXXXX)"
```

현재 저장 결과 근거와 기여행 일괄 조회까지 포함한 UCI 파이프라인은 실제 원본 파일이 있는 환경에서 관련 `tests/test_pipeline.py` 검사로 확인한다. 실제 `5→53` 선택 범위, 표시 혼합·합계 0·큰 정수, 다른 실행 행 비혼입, 대사 실패 뒤 last-good, 미등록·실패·처리 중·외부 경로·손상 결과 반례를 포함한다. 별도 원천 의미 조사와 Kafka 코드·테스트·실제 broker 실험은 이번 변경과 무관해 다시 실행하지 않았으며, 이 검사 통과가 broker 실행을 대신하지 않는다.

검사는 다음 주장을 서로 다른 근거로 확인한다.

- 보존 원본 SHA와 manifest가 일치한다.
- DuckDB SQL 결과가 openpyxl만 사용한 독립 Python 합계와 일치한다.
- 같은 입력을 다시 처리해도 원본 행 수와 상품 결과가 불어나지 않는다.
- 같은 원본에서 `[2,3] → [3] → [3,2]`로 범위를 바꿔도 각 실행의 결과·기여 행만 계산되고, 같은 행 집합은 같은 범위 지문을 가진다.
- 실패한 새 범위는 현재 정상 결과나 비교 가능한 성공 결과 목록에 들어가지 않는다.
- 비교 화면은 값 변화와 기여 원본 위치 변화를 분리하고, 선택 범위·파일·시트·SQL 기준의 같음·다름·일부 확인 불가와 다음 확인 근거를 연결한다.
- 숫자형 InvoiceNo는 원문 문자열과 `integer` 종류를 보존한 채 `false`, C/c 접두는 `true`, 누락·공백·지원하지 않는 셀 타입은 `unknown`으로 관찰하고 표본 합계를 바꾸지 않는다.
- 원천 변환 SQL과 집계 SQL의 지문을 따로 남기며, 이전 JSON에 원천 표시가 없으면 미계산/미확인으로 처리한다.
- 내용이 같아도 원본 위치가 다른 두 행은 업무 중복으로 추정해 지우지 않고 둘 다 합산한다.
- 상품 하나를 고르면 다른 상품 행이 기여 행에 섞이지 않는다.
- 정수로 손실 없이 표현할 수 없는 수량·읽기 실패·DB 적재 뒤 실패·JSON 작성 뒤 실패가 마지막 정상 표시를 덮지 않는다.
- 준비 신호를 낸 별도 프로세스를 DB commit·artifact·게시 경계에서 실제로 강제 종료해 rollback, 중단 이력 복구, 미게시 artifact 격리, 게시 성공 유지가 DB 증거와 일치한다.
- 살아 있는 writer가 잠금을 가진 동안 두 번째 writer가 실행되지 않고, 시간 추정으로 첫 실행을 실패 처리하지 않는다.
- 상세 단가는 임의의 소수 자릿수로 반올림하거나 자르지 않고 원본에서 읽은 문자열을 보여 준다. 금액 계산·반올림 규칙은 아직 정하지 않았다.
- 수량 `0`이 있는 행과 해당 상품 행이 아예 없는 상태를 구별한다.
- 생성 JSON과 화면 데이터에 `CustomerID`가 포함되지 않는다.
- Kafka 합성 이벤트의 같은 ID·같은 내용은 offset이 같거나 달라도 sink 합계를 늘리지 않고, 같은 ID·다른 내용은 충돌로 멈춰 committed offset을 전진시키지 않는다.
- 실제 broker에서 sink commit 뒤 offset commit 전 consumer를 강제 종료하면 같은 group이 같은 offset을 다시 읽으며, 최종 sink가 독립 Python·SQL 배치와 일치한다.
- broker group이 앞선 새 빈 sink는 실패하고, sink와 broker가 함께 offset `5`까지 간 뒤 로컬 audit 전에 종료된 실행은 재시작 때 성공 위치를 되돌리거나 이벤트를 중복 적용하지 않는다.
- 날짜만 있거나 timezone 없는 발생시각과 signed BIGINT 밖의 단일 수량을 저장 전에 거부하며, 두 `BIGINT_MAX`의 합도 축소 없이 독립 계산과 일치한다.
- 발생시각 역순과 늦은 입력을 전달 순서와 구별하고, 늦은 이벤트가 바꾼 UTC 날짜를 결과에서 확인한다.

## 아직 구현하지 않은 것

- UCI 전체 파일의 품질 조사와 데이터셋 최종 채택
- 판매·취소 원거래 연결, 중복 거래 판별, 영업일·시간대, 금액 반올림 계약
- dbt·Airflow·PostgreSQL 채택
- Kafka 여러 partition·consumer rebalance, schema 진화, 외부 DB와 offset의 원자적 exactly-once 처리
- Spark의 통제된 규모·분포·실행 계획 비교
- 실제 운영 DB, 클라우드, 배포

2026-09-23 당시 다음 재개 지점이었던 `실제 Kafka 재전달 실험`과 `kafka_sink_checkpoint` 검증은 위 범위에서 완료됐다. 현재 비교 화면 다음의 업무 질문은 `D / Discount / -1`의 분류와 기준 날짜이며, Kafka의 여러 consumer와 규모 비교는 이 의미 선택과 독립된 미검증 학습 갈래다.

다음 도구는 이름 순서로 붙이지 않는다. PostgreSQL은 Kafka offset과 별개인 다중 프로세스 sink·서비스 제공 경계를 실제로 비교할 필요가 있을 때, Spark는 같은 결과의 규모 비교가 필요할 때 연다.

## 데이터 출처와 공개 범위

**원천:** Chen, D. (2015). *Online Retail* [Dataset]. UCI Machine Learning Repository. [DOI: 10.24432/C5BW33](https://doi.org/10.24432/C5BW33). [공식 데이터 설명](https://archive.ics.uci.edu/dataset/352/online+retail)은 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)을 명시한다. 라이선스의 보증 부인 등 조건은 링크한 원문을 따른다.

공개 코드에는 원본 Excel·로컬 DB·생성 JSON·고객 식별값·개인 실행 경로를 포함하지 않는다. 테스트의 `ROWS`는 공개 원천 일부를 바탕으로 고객 식별자를 가상 값 `TEST-CUSTOMER`로 교체하고 0·누락·잘못된 타입·공백 등 반례를 추가한 가공 자료다. 해당 원천에서 유래한 데이터에는 위 출처와 CC BY 4.0이 적용되며 실제 전체 원천 또는 Kafka 사건 자료로 보지 않는다. 로컬에서 별도로 받은 원본과 DB에는 원천 `CustomerID`가 남을 수 있으나 결과 JSON·화면에는 제공하지 않는다.

[GitHub 첫 게시](https://github.com/junhyun-dev/shopping-data-platform/tree/6111db04ba383d9098665bd5f9682de7c7bf5c4c)는 원본 추적·선택 범위 재처리·원천 취소 표시·프로세스 중단 복구까지 검증한 로컬 구현이다. 현재 코드에는 별도로 검증한 Kafka 합성 이벤트·체크포인트·재전달 실험도 포함한다. Apache Kafka 배포 파일, broker 데이터, 로컬 실행 결과는 Git에 포함하지 않는다. 공개 웹 서비스나 운영 환경 배포는 아니다.

프로젝트 코드의 재사용 라이선스는 아직 지정하지 않았다. 데이터의 CC BY 4.0을 코드 전체의 라이선스로 해석하지 않는다.
