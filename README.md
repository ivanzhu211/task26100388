# task26100388

## docxcheck：DOCX 入库核验库

供奖学金材料收集流程在入库前调用：确认 ZIP 容器完整，且其中确实包含
可识别的 OOXML Transitional 非宏 DOCX 主文档。只读核验，不修改原文件，
不做上传接口或预览。

### 用法

```python
from docxcheck import verify_docx

verdict = verify_docx("/path/to/submission.bin")  # 不依赖扩展名
if verdict.accepted:
    ...  # 入库
else:
    print(verdict.reason)  # 拒绝原因
```

### 核验范围

- ZIP 容器：Stored/Deflate、带/不带签名的数据描述符、归档注释
  （注释中的伪 EOCD 签名不会造成误判）；拒绝 ZIP64、分卷、加密、
  自解压前缀、EOCD 及其注释之后的额外数据。
- 结构一致性：中央目录 / 局部文件头 / 数据描述符的名称、标志、压缩
  方式、大小、CRC 两两相容；所有结构区间不得越界或重叠；实际解压
  长度与 CRC 必须匹配声明。
- 文件名：UTF-8 标志置位按 UTF-8 解释，否则仅 ASCII；拒绝重复名、
  绝对路径、反斜杠、空字符、空路径段与点路径段。
- 包结构：必须含 `[Content_Types].xml` 与 `_rels/.rels`；恰有一个
  内部 officeDocument 关系，目标为无百分号编码的规范相对路径
  （允许一个起始斜杠），不固定假设 `word/document.xml`。
- 主文档：内容类型必须是非宏 DOCX 主文档类型（覆盖优先于扩展名
  默认，重复声明拒绝）；XML 良构、禁 DTD/实体声明；根元素为
  Transitional 命名空间中的 `document`。
- 限额：文件 ≤ 10 MiB，条目 ≤ 1024，实际展开总量 ≤ 64 MiB
  （按真实数据计），单文件核验峰值内存增量 ≤ 24 MiB。

### 测试

```sh
python3 -m unittest discover -s tests
```

覆盖：合法文档的不同主文档路径 / XML 前缀 / 描述符形式 / 含伪结束
签名注释；元数据矛盾、区间重叠、截断压缩流、关系指错等拒绝场景；
接近 64 MiB 的高压缩文档通过且内存达标、真实展开超限与声明大小
造假拒绝。
