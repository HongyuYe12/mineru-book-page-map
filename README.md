# MinerU 本地书籍转 Markdown 工具

这个目录提供一个可重复运行的本地流程：MinerU 负责 PDF 解析，`mineru_book.py` 负责把解析结果整理成可检索 Markdown，并维护 PDF 页码对应关系。

## 产物

对 `book.pdf` 执行转换后，输出目录会包含：

- `book.md`：带 `<!-- pdf-page: N -->` 隐形页码标记的 Markdown。搜索结果可以直接定位到原 PDF 页。
- `book.page-map.md`：人类可读的页码映射表，含 Markdown 行号、匹配来源和文本片段。
- `book.page-map.json`：供脚本或其他检索系统使用的结构化索引。
- `mineru_raw/`：MinerU 原始输出，保留 `middle_json.json`、图片和中间文件，便于核对。
- `mineru.log.txt`：MinerU 的标准输出和错误输出。

转换结束前会自动执行严格验收：所有 PDF 页必须有 Markdown 页码标记，并且所有 Markdown 图片引用必须存在。验收失败时命令返回错误，不会把不完整结果当成成功。

脚本优先使用 MinerU 4 的 `middle_json.json`，从每页多个内容块中寻找可靠 Markdown 锚点；同时兼容旧版 `*_content_list.json`、已有页码注释和 PDF 文本回退。扫描版 PDF 若 OCR 文本和 Markdown 差异很大，严格验收会失败，不会伪造页码。

## Windows PowerShell 部署

建议 Python 3.10 或更高版本。首次安装：

```powershell
cd C:\Users\hongy\Desktop\MinerU\mineru-book-tool
.\setup.ps1
```

如果只想先安装页码处理脚本的依赖而暂时不装 MinerU：

```powershell
.\setup.ps1 -SkipMineru
```

安装脚本会安装 MinerU 4.x（`mineru>=4.0,<5`）。模型会在首次解析时按需下载，可能需要较长时间和较大磁盘空间。中国大陆网络环境下可以在运行前设置 `$env:MINERU_MODEL_SOURCE="modelscope"`。

## 转换一本书

```powershell
.\run.ps1 -Pdf "D:\books\book.pdf" -OutputDir "D:\books\book-output"
```

等价的直接调用：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py convert "D:\books\book.pdf" `
  --output-dir "D:\books\book-output" --backend auto --clean
```

脚本默认调用虚拟环境里的 `mineru-kit.exe`。如果命令位于其他位置，可以传完整路径，例如：

```powershell
python .\mineru_book.py convert .\book.pdf -o .\book-output --mineru-command "D:\MinerU\mineru-kit.exe"
```

脚本优先执行 MinerU 4 的 `mineru-kit parse PDF -o result.zip --format zip --tier standard`，从 ZIP 中同时读取 `markdown.md`、`middle_json.json` 和图片；如果失败，再尝试旧版 MinerU 3 命令。

## 按内容查 PDF 页

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py search .\book-output "量子纠缠"
```

输出示例：

```text
PDF page 37 | Markdown line 812 | 量子纠缠可以通过……
```

查询某个 PDF 页的映射记录：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py page .\book-output 37
```

## 验证输出完整性

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py verify .\book-output
```

输出包含总页数、匹配页数、匹配率、缺失或重复页码标记、图片引用数和损坏图片列表。完全通过时 `ok` 为 `true`。

## 不重新识别，重建页码和图片

如果已经有 `mineru_raw`，可以直接修复最终 Markdown，无需再次运行耗时的模型推理：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py rebuild .\book.pdf -o .\book-output
```

`rebuild` 会从 MinerU 4 的 `middle_json.json` 逐页、逐内容块寻找可靠锚点，插入 `<!-- pdf-page: N -->`，复制图片到输出目录，再自动执行严格验收。

## 适合接入检索系统的约定

将 `book.md` 作为全文检索源；索引时保留每个命中段落前最近的 `<!-- pdf-page: N -->` 标记，或者直接读取 `book.page-map.json`。回复用户时可输出“PDF 第 N 页”，并把 `book.page-map.md` 作为人工核对入口。页码是 PDF 物理页码（从 1 开始），不是书内印刷页码；如果没有可用页码标记，搜索命令会明确输出 `PDF page unknown`，不会错误地默认成第 1 页。

## 后续路线

- 批量转换整个文件夹。
- 增加书内印刷页码与 PDF 物理页码双重映射。
- 提供选择文件并点击转换的简单图形界面。
- 为长书生成章节索引，方便接入 AI/RAG 检索。

## 常见问题

**没有生成页码映射？** 先运行 `verify`，再检查 `mineru_raw/extracted/middle_json.json` 和 `mineru.log.txt`。已有原始结果时可运行 `rebuild`，无需重新推理。

**MinerU 参数报错？** 先运行 `.\.venv\Scripts\mineru-kit.exe parse --help`，然后通过 `--mineru-command` 传入可执行文件完整路径。脚本优先使用 MinerU 4，并保留旧版 MinerU 3 的兼容回退。

**如何更新？** 在虚拟环境中运行 `.\.venv\Scripts\python.exe -m pip install --upgrade "mineru>=4.0,<5"`，再用同一个输出目录重新执行转换。建议保留旧输出目录，便于比较页码映射变化。
