# 上传测试文档说明

这些文件用于手工测试上传、解析、向量入库、问答和错误处理。二进制文档由上级目录的 `generate_sample_documents.py` 生成；需要重新生成时，在 `backend` 目录执行：

```powershell
uv run python tests/fixtures/generate_sample_documents.py
```

| 文件 | 类型 | 建议验证场景 |
| --- | --- | --- |
| `01-年假制度.txt` | TXT | 基础上传；询问“满一年有几天年假”。 |
| `02-信息安全规范.md` | Markdown | 标题与段落解析；询问密码长度、锁屏要求。 |
| `03-员工手册.docx` | Word | DOCX 标题与正文解析；询问打卡和病假规则。 |
| `04-培训计划.xlsx` | Excel | 多工作表解析；询问培训日期、联系人。 |
| `05-远程办公指南.pdf` | PDF | 双页 PDF；询问远程办公天数、VPN 要求，并检查页码引用。 |
| `06-重复内容-A.txt`、`07-重复内容-B.txt` | TXT | 向同一知识库先后上传，第二次应返回重复文件错误。 |
| `08-空白内容.txt` | TXT | 上传后处理失败，错误码应为 `DOCUMENT_CONTENT_EMPTY`。 |
| `09-不支持格式.csv` | CSV | 上传应直接返回 `415 UNSUPPORTED_FILE_TYPE`。 |
| `10-损坏的PDF.pdf` | PDF | 上传后解析失败，验证安全错误返回与任务状态。 |
| `11-超过20MB限制.txt` | TXT | 上传应直接返回 `413 FILE_TOO_LARGE`。 |

测试结束后，可删除临时知识库和数据库中的测试数据，避免影响后续演示检索结果。
