# mineru-book-page-map

调用本地 MinerU 将 PDF 转成 Markdown，并保留可追溯的 PDF 物理页码。

项目目前完成文档解析和页码映射，后续可以沿 NLP / 多模态文档问答方向扩展：

```text
PDF
  -> OCR 与版面解析
  -> Markdown 与页码映射
  -> 文本切分与检索
  -> 带 PDF 页码引用的论文或书籍问答
```

## 输出文件

转换 `book.pdf` 后，输出目录包含：

| 文件 | 用途 |
| --- | --- |
| `book.md` | 最终 Markdown，含 `<!-- pdf-page: N -->` 页码标记 |
| `book.page-map.md` | PDF 页码、Markdown 行号和文本片段对照表 |
| `book.page-map.json` | 供程序读取的页码索引 |
| `images/` | Markdown 引用的图片 |
| `mineru_raw/` | MinerU 原始输出 |
| `mineru.log.txt` | MinerU 运行日志 |

转换结束时会检查页码标记是否完整、有序，以及 Markdown 引用的本地图片是否存在。检查失败时命令返回错误。

## 安装

在 PowerShell 中运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd C:\path\to\mineru-book-page-map
.\setup.ps1
```

`setup.ps1` 会创建 `.venv` 并安装 MinerU 4。只安装页码处理依赖时使用：

```powershell
.\setup.ps1 -SkipMineru
```

国内网络可在转换前设置：

```powershell
$env:MINERU_MODEL_SOURCE="modelscope"
```

## 转换 PDF

```powershell
.\run.ps1 -Pdf "D:\books\book.pdf" -OutputDir "D:\books\book-output"
```

直接调用 Python：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py convert "D:\books\book.pdf" `
  -o "D:\books\book-output" --clean
```

首次运行可能需要下载模型。

## 常用命令

搜索正文并显示 PDF 页码：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py search .\book-output "关键词"
```

查看某一页的索引记录：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py page .\book-output 37
```

检查页码和图片：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py verify .\book-output
```

已有 `mineru_raw/` 时重新生成页码和图片，不再次运行 MinerU：

```powershell
.\.venv\Scripts\python.exe .\mineru_book.py rebuild .\book.pdf -o .\book-output
```

## 页码来源

程序优先读取 MinerU 4 的 `middle_json.json`，用每页内容块在 Markdown 中定位分页点。旧版 `content_list.json`、Markdown 自带页码标记和 PDF 文本提取作为兼容路径。

这里的页码是 PDF 物理页码，从 1 开始，不等同于书中印刷页码。无法确认页码时，搜索结果显示 `PDF page unknown`。

## 开发

```powershell
$env:PYTHONPATH=(Get-Location).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前测试覆盖页码锚点匹配、损坏图片检测和未知页码搜索结果。
