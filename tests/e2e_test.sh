#!/usr/bin/env bash
# WebDAV 端到端功能测试。用法: e2e_test.sh <base_url> <user:pass>
set -u
B=${1:-http://127.0.0.1:8080}
U=${2:?需要传入 user:pass}

PASS=0; FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 断言: <描述> <期望状态码> <实际状态码> [比对文件1 比对文件2]
check() {
    local desc=$1 expect=$2 actual=$3
    if [[ "$expect" == "$actual" ]]; then
        PASS=$((PASS+1)); printf '  ✓ %-34s %s\n' "$desc" "$actual"
    else
        FAIL=$((FAIL+1)); printf '  ✗ %-34s 期望 %s 实际 %s\n' "$desc" "$expect" "$actual"
    fi
}
cmp_files() {
    if cmp -s "$1" "$2"; then PASS=$((PASS+1)); printf '  ✓ %-34s\n' "内容一致"
    else FAIL=$((FAIL+1)); printf '  ✗ %-34s\n' "内容不一致"; fi
}

head -c 100000 /dev/urandom > "$TMP/big.bin"
echo "hello webdav v1" > "$TMP/note.txt"
echo "中文内容 hello" > "$TMP/中文 文件.txt"
echo "updated v2" > "$TMP/note-v2.txt"

echo "== 认证 =="
check "无认证"            401 "$(curl -s -o /dev/null -w '%{http_code}' $B/)"
check "错误密码"          401 "$(curl -s -o /dev/null -w '%{http_code}' -u "${U%:*}:wrongpwd" $B/)"

echo "== 目录操作 =="
check "PROPFIND 根目录"   207 "$(curl -s -u $U -X PROPFIND -H 'Depth: 1' -o /dev/null -w '%{http_code}' $B/)"
check "MKCOL 目录"        201 "$(curl -s -u $U -X MKCOL -o /dev/null -w '%{http_code}' $B/t1)"
check "重复 MKCOL → 405"  405 "$(curl -s -u $U -X MKCOL -o /dev/null -w '%{http_code}' $B/t1)"
check "父级缺失 MKCOL → 409" 409 "$(curl -s -u $U -X MKCOL -o /dev/null -w '%{http_code}' $B/no-parent/sub)"
check "MKCOL 二级目录"    201 "$(curl -s -u $U -X MKCOL -o /dev/null -w '%{http_code}' $B/t1/sub)"

echo "== 文件读写 =="
check "PUT 100KB 二进制"  201 "$(curl -s -u $U -T $TMP/big.bin -o /dev/null -w '%{http_code}' $B/t1/sub/big.bin)"
check "GET 下载"          200 "$(curl -s -u $U -o $TMP/dl.bin -w '%{http_code}' $B/t1/sub/big.bin)"; cmp_files "$TMP/big.bin" "$TMP/dl.bin"
check "PUT 文本"          201 "$(curl -s -u $U -T $TMP/note.txt -o /dev/null -w '%{http_code}' $B/t1/note.txt)"
CN_URL=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=':/'))" "$B/t1/中文 文件.txt")
check "PUT 中文文件名"    201 "$(curl -s -u $U -T "$TMP/中文 文件.txt" -o /dev/null -w '%{http_code}' "$CN_URL")"
check "GET 中文文件名"    200 "$(curl -s -u $U -o "$TMP/dl-cn.txt" -w '%{http_code}' "$CN_URL")"; cmp_files "$TMP/中文 文件.txt" "$TMP/dl-cn.txt"
check "覆盖 PUT → 204"    204 "$(curl -s -u $U -T $TMP/note-v2.txt -o /dev/null -w '%{http_code}' $B/t1/note.txt)"
curl -s -u $U $B/t1/note.txt > "$TMP/dl-note.txt"
grep -q "updated v2" "$TMP/dl-note.txt" && { PASS=$((PASS+1)); echo "  ✓ 覆盖后内容正确"; } || { FAIL=$((FAIL+1)); echo "  ✗ 覆盖后内容错误"; }
check "PUT 到不存在目录 → 409" 409 "$(curl -s -u $U -T $TMP/note.txt -o /dev/null -w '%{http_code}' $B/no-such-dir/f.txt)"

echo "== COPY / MOVE =="
check "COPY 文件"         201 "$(curl -s -u $U -X COPY -H "Destination: $B/t1/note-copy.txt" -o /dev/null -w '%{http_code}' $B/t1/note.txt)"
check "COPY 后源仍在"     200 "$(curl -s -u $U -o /dev/null -w '%{http_code}' $B/t1/note.txt)"
check "MOVE 文件"         201 "$(curl -s -u $U -X MOVE -H "Destination: $B/t1/note-moved.txt" -o /dev/null -w '%{http_code}' $B/t1/note-copy.txt)"
check "MOVE 后源 404"     404 "$(curl -s -u $U -o /dev/null -w '%{http_code}' $B/t1/note-copy.txt)"
check "MOVE 目录"         201 "$(curl -s -u $U -X MOVE -H "Destination: $B/t2" -o /dev/null -w '%{http_code}' $B/t1/sub)"
check "MOVE 后文件跟随"   200 "$(curl -s -u $U -o /dev/null -w '%{http_code}' $B/t2/big.bin)"

echo "== 目录列举 =="
LIST=$(curl -s -u $U -X PROPFIND -H 'Depth: 1' $B/t1 | grep -oE '<[a-z0-9]+:href>[^<]*</[a-z0-9]+:href>')
for expect in "/t1/" "/t1/note.txt" "/t1/note-moved.txt"; do
    echo "$LIST" | grep -qF "<ns0:href>$expect</ns0:href>" && { PASS=$((PASS+1)); printf '  ✓ 列出 %-28s\n' "$expect"; } || { FAIL=$((FAIL+1)); printf '  ✗ 未列出 %s\n' "$expect"; }
done
CN_HREF=$(python3 -c "import urllib.parse; print(urllib.parse.quote('/t1/中文 文件.txt', safe='/'))")
echo "$LIST" | grep -qF "<ns0:href>$CN_HREF</ns0:href>" && { PASS=$((PASS+1)); printf '  ✓ 列出 %-28s\n' "$CN_HREF"; } || { FAIL=$((FAIL+1)); printf '  ✗ 未列出 %s\n' "$CN_HREF"; }
COUNT=$(echo "$LIST" | wc -l)
[[ $COUNT -eq 4 ]] && { PASS=$((PASS+1)); echo "  ✓ 成员数量正确 (4)"; } || { FAIL=$((FAIL+1)); echo "  ✗ 成员数量 $COUNT，期望 4"; }

echo "== 条件请求与 Range =="
check "If-Modified-Since → 304" 304 "$(curl -s -u $U -H 'If-Modified-Since: Wed, 01 Jan 2098 00:00:00 GMT' -o /dev/null -w '%{http_code}' $B/t2/big.bin)"
check "Range 0-9999 → 206" 206 "$(curl -s -u $U -H 'Range: bytes=0-9999' -o $TMP/r1 -w '%{http_code}' $B/t2/big.bin)"; cmp_files <(head -c 10000 $TMP/big.bin) $TMP/r1
check "Range 50000- → 206" 206 "$(curl -s -u $U -H 'Range: bytes=50000-' -o $TMP/r2 -w '%{http_code}' $B/t2/big.bin)"; cmp_files <(tail -c +50001 $TMP/big.bin) $TMP/r2
check "Range -500 后缀 → 206" 206 "$(curl -s -u $U -H 'Range: bytes=-500' -o $TMP/r3 -w '%{http_code}' $B/t2/big.bin)"; cmp_files <(tail -c 500 $TMP/big.bin) $TMP/r3
check "Range 越界 → 416"  416 "$(curl -s -u $U -H 'Range: bytes=200000-' -o /dev/null -w '%{http_code}' $B/t2/big.bin)"
ETAG=$(curl -s -u $U -I $B/t2/big.bin | grep -i etag | tr -d '\r' | awk '{print $2}')
check "If-Range 匹配 → 206" 206 "$(curl -s -u $U -H 'Range: bytes=0-9' -H "If-Range: $ETAG" -o /dev/null -w '%{http_code}' $B/t2/big.bin)"
check "If-Range 不匹配 → 200" 200 "$(curl -s -u $U -H 'Range: bytes=0-9' -H 'If-Range: "deadbeef"' -o /dev/null -w '%{http_code}' $B/t2/big.bin)"
check "HEAD → 200"        200 "$(curl -s -u $U -I -o /dev/null -w '%{http_code}' $B/t2/big.bin)"
curl -s -u $U -I $B/t2/big.bin | grep -qi 'Accept-Ranges: bytes' && { PASS=$((PASS+1)); echo "  ✓ Accept-Ranges: bytes"; } || { FAIL=$((FAIL+1)); echo "  ✗ 缺 Accept-Ranges"; }

echo "== 锁 =="
LOCK_BODY='<?xml version="1.0" encoding="utf-8"?><D:lockinfo xmlns:D="DAV:"><D:lockscope><D:exclusive/></D:lockscope><D:locktype><D:write/></D:locktype><D:owner><D:href>tester</D:href></D:owner></D:lockinfo>'
LOCK_RESP=$(curl -s -u $U -X LOCK -H 'Content-Type: application/xml' -H 'Timeout: Second-3600' -d "$LOCK_BODY" -w '\n%{http_code}' $B/t1/note.txt)
LOCK_CODE=$(echo "$LOCK_RESP" | tail -1)
check "LOCK 文件"        200 "$LOCK_CODE"
LOCK_TOKEN=$(echo "$LOCK_RESP" | grep -oE 'opaquelocktoken:[0-9a-fx-]+' | head -1)
if [[ -n "$LOCK_TOKEN" && "$LOCK_CODE" == "200" ]]; then
    check "UNLOCK"        204 "$(curl -s -u $U -X UNLOCK -H "Lock-Token: <$LOCK_TOKEN>" -o /dev/null -w '%{http_code}' $B/t1/note.txt)"
else
    check "UNLOCK"        204 "-（无锁令牌，跳过）"
    FAIL=$((FAIL+1)); echo "  ✗ 未获取锁令牌"
fi

echo "== 删除 =="
check "DELETE 文件"       204 "$(curl -s -u $U -X DELETE -o /dev/null -w '%{http_code}' $B/t1/note.txt)"
check "重复 DELETE → 404" 404 "$(curl -s -u $U -X DELETE -o /dev/null -w '%{http_code}' $B/t1/note.txt)"
check "递归 DELETE 目录"  204 "$(curl -s -u $U -X DELETE -o /dev/null -w '%{http_code}' $B/t2)"
check "删除后 PROPFIND → 404" 404 "$(curl -s -u $U -X PROPFIND -o /dev/null -w '%{http_code}' $B/t2)"

echo
echo "结果: $PASS 通过, $FAIL 失败"
exit $((FAIL > 0))
