import { useEffect, useRef, useState } from "react";
import { DiffEditor } from "@monaco-editor/react";
import { SEVERITY_META } from "../constants";

const SEVERITY_GLYPH_CLASS = {
  CRITICAL: "monaco-glyph-critical",
  HIGH: "monaco-glyph-high",
  MEDIUM: "monaco-glyph-medium",
  LOW: "monaco-glyph-low",
  INFO: "monaco-glyph-info",
};

const EXT_LANGUAGE = {
  py: "python",
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  json: "json",
  css: "css",
  html: "html",
  md: "markdown",
  yml: "yaml",
  yaml: "yaml",
  sh: "shell",
  go: "go",
  java: "java",
  rb: "ruby",
  rs: "rust",
  c: "c",
  cpp: "cpp",
  h: "c",
};

function languageForFilename(filename) {
  const ext = filename.split(".").pop()?.toLowerCase();
  return EXT_LANGUAGE[ext] ?? "plaintext";
}

export default function DiffViewer({ files, findings, jumpTarget }) {
  const [selectedFile, setSelectedFile] = useState(files?.[0]?.filename ?? null);
  const editorRef = useRef(null);
  const monacoRef = useRef(null);
  const decorationsRef = useRef([]);

  const file = files.find((f) => f.filename === selectedFile);
  const fileFindings = findings.filter((f) => f.location.file === selectedFile);

  function applyDecorations() {
    const diffEditor = editorRef.current;
    const monaco = monacoRef.current;
    if (!diffEditor || !monaco) return;
    const modifiedEditor = diffEditor.getModifiedEditor();

    const decorations = fileFindings.map((finding) => {
      const lineNumber = finding.location.line_start;
      const glyphClass = SEVERITY_GLYPH_CLASS[finding.severity] ?? SEVERITY_GLYPH_CLASS.INFO;
      return {
        range: new monaco.Range(lineNumber, 1, lineNumber, 1),
        options: {
          isWholeLine: true,
          glyphMarginClassName: `monaco-glyph ${glyphClass}`,
          glyphMarginHoverMessage: {
            value: `**${SEVERITY_META[finding.severity]?.label ?? finding.severity}**: ${finding.title}`,
          },
        },
      };
    });

    decorationsRef.current = modifiedEditor.deltaDecorations(decorationsRef.current, decorations);
  }

  useEffect(() => {
    applyDecorations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFile, files, findings]);

  useEffect(() => {
    if (!jumpTarget) return;
    if (jumpTarget.file !== selectedFile) {
      setSelectedFile(jumpTarget.file);
      return; // effect re-runs once selectedFile actually changes
    }
    const diffEditor = editorRef.current;
    const monaco = monacoRef.current;
    if (!diffEditor || !monaco) return;
    const modifiedEditor = diffEditor.getModifiedEditor();
    const lineNumber = jumpTarget.lineStart;
    modifiedEditor.revealLineInCenter(lineNumber);
    modifiedEditor.setSelection(new monaco.Range(lineNumber, 1, lineNumber, 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpTarget, selectedFile]);

  if (!files || files.length === 0) {
    return (
      <div style={{ padding: 20, color: "var(--fg-muted)", fontSize: 13 }}>
        No diff available for this review.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          display: "flex",
          gap: 4,
          padding: "8px 12px",
          borderBottom: "1px solid var(--border-muted)",
          overflowX: "auto",
          flexShrink: 0,
        }}
      >
        {files.map((f) => (
          <button
            key={f.filename}
            onClick={() => setSelectedFile(f.filename)}
            className="mono"
            style={{
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: "4px 10px",
              fontSize: 12,
              background: f.filename === selectedFile ? "var(--accent-subtle)" : "var(--canvas)",
              color: f.filename === selectedFile ? "var(--accent-emphasis)" : "var(--fg-muted)",
              cursor: "pointer",
              whiteSpace: "nowrap",
              flexShrink: 0,
            }}
          >
            {f.filename}
          </button>
        ))}
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <DiffEditor
          height="100%"
          language={file ? languageForFilename(file.filename) : "plaintext"}
          original={file?.original ?? ""}
          modified={file?.modified ?? ""}
          theme="vs"
          options={{
            readOnly: true,
            renderSideBySide: true,
            minimap: { enabled: false },
            glyphMargin: true,
            fontSize: 12,
            fontFamily: "var(--font-mono)",
            scrollBeyondLastLine: false,
            renderOverviewRuler: false,
          }}
          onMount={(editor, monaco) => {
            editorRef.current = editor;
            monacoRef.current = monaco;
            applyDecorations();
          }}
        />
      </div>
    </div>
  );
}