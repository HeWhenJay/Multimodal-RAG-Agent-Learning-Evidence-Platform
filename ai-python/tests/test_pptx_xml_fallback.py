from io import BytesIO
import builtins
import zipfile

from rag.loaders.document_parsers import DocumentParserRouter


def test_pptx_xml_fallback_parses_without_python_pptx(monkeypatch):
    def build_pptx_bytes() -> bytes:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
                  <Default Extension="xml" ContentType="application/xml"/>
                  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
                  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
                </Types>
                """,
            )
            archive.writestr(
                "_rels/.rels",
                """<?xml version="1.0" encoding="UTF-8"?>
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
                </Relationships>
                """,
            )
            archive.writestr(
                "ppt/presentation.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
                  <p:sldIdLst>
                    <p:sldId id="256" r:id="rId1"/>
                  </p:sldIdLst>
                </p:presentation>
                """,
            )
            archive.writestr(
                "ppt/slides/slide1.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
                <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
                  <p:cSld>
                    <p:spTree>
                      <p:sp>
                        <p:txBody>
                          <a:p><a:r><a:t>RAG PPTX XML 降级解析测试</a:t></a:r></a:p>
                          <a:p><a:r><a:t>第一段内容用于验证标题提取、正文提取和切块前文本汇总，确保 XML 降级解析不会退回到乱码文本，并且每个段落都能保持原始阅读顺序。</a:t></a:r></a:p>
                          <a:p><a:r><a:t>第二段内容继续补足字符长度，确保质量评分保持在 READY 区间，同时验证分页幻灯片文本的顺序保持稳定，避免被误判成无效压缩包文本。</a:t></a:r></a:p>
                          <a:p><a:r><a:t>第三段内容用于进一步增加可索引文本，避免因为文本过短而触发 PARTIAL 状态，也验证摘要索引会正常生成，章节摘要也能正常汇总。</a:t></a:r></a:p>
                          <a:p><a:r><a:t>第四段内容补充更多关键字，例如 RAG、切块、摘要、embedding、向量入库、检索、rerank 和 evidence 引用，以便冒烟测试更接近真实课程 PPT。</a:t></a:r></a:p>
                          <a:p><a:r><a:t>第五段内容继续扩充文本总量，确保 nativeTextChars 超过质量阈值，并验证 python-pptx 缺失时的 XML 降级路径仍然可以被标记为 READY。</a:t></a:r></a:p>
                        </p:txBody>
                      </p:sp>
                    </p:spTree>
                  </p:cSld>
                </p:sld>
                """,
            )
        return buffer.getvalue()

    orig_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pptx" or name.startswith("pptx."):
            raise ModuleNotFoundError("No module named 'pptx'")
        return orig_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    parser = DocumentParserRouter()
    parsed = parser.parse_bytes(
        content=build_pptx_bytes(),
        filename="fallback-test.pptx",
        document_id="doc-pptx-fallback",
        source_title="PPTX XML 降级测试",
        document_type="pptx",
        source_path="uploads/rag/fallback-test.pptx",
    )

    assert parsed.status == "READY"
    assert parsed.parser == "pptx-xml-fallback"
    assert parsed.blocks
    assert parsed.parse_quality.nativeTextChars > 80
    assert any(block.blockType == "heading" for block in parsed.blocks)
    assert any(block.blockType == "text" for block in parsed.blocks)
    assert any("python-pptx parser failed" in warning for warning in parsed.warnings)
