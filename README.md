# task26100388

DOCX 入库核验库：在奖学金材料入库前确认文件是容器完整、包含可识别主文档的
OOXML Transitional 非宏 DOCX。只读核验，不修改原文件，不提供上传或预览。

## 用法

```python
from docx_verify import verify_docx

result = verify_docx("/path/to/submission.docx")   # 也接受 bytes
if result.ok:
    ...  # 允许入库
else:
    print(result.reason)  # 机器可读的拒绝原因，如 "crc_mismatch"
```

## 核验范围

- ZIP 容器：Stored/Deflate、带/不带签名的数据描述符、归档注释；拒绝 ZIP64、
  分卷、加密、自解压前缀、EOCD 之后的额外数据；中央目录/局部头/描述符信息
  必须一致，条目区间不得越界或重叠；实际解压长度与 CRC 必须匹配声明。
- 条目名：UTF-8 标志置位按 UTF-8，否则仅 ASCII；拒绝重名、绝对路径、反斜杠、
  空字符、空/点/双点路径段。
- 包结构：`[Content_Types].xml`、`_rels/.rels`、恰一个内部 officeDocument 关系、
  非宏主文档内容类型（Override 优先于 Default）、主文档根元素为 Transitional
  命名空间的 `document`；所有相关 XML 须良构且不含 DTD/实体声明。
- 限额：文件 ≤ 10 MiB，条目 ≤ 1024，实际展开总量 ≤ 64 MiB（按真实数据计），
  单文件核验峰值内存增量 < 24 MiB（全程流式处理）。

## 测试

```bash
python3 -m pytest tests/ -q
```

- `tests/test_valid.py`：合法文档（不同主文档路径、XML 前缀、描述符形式、
  含伪 EOCD 签名的注释、打乱的中央目录顺序等）均通过。
- `tests/test_invalid.py`：元数据矛盾、区间重叠、截断压缩流、关系指错等拒绝。
- `tests/test_limits.py`：接近 64 MiB 的高压缩文档通过且内存达标；真实展开
  超限、声明大小造假、文件/条目超限均拒绝。
