import { readFileSync } from "node:fs";
import ts from "typescript";

// The pinned TypeScript parser also reads the project's JavaScript and ES modules.
export function scan(path, source) {
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true);
  const signals = [];
  const lineAt = pos => file.getLineAndCharacterOfPosition(pos).line + 1;

  const [error] = file.parseDiagnostics;
  if (error) {
    const message = ts.flattenDiagnosticMessageText(error.messageText, " ");
    throw new SyntaxError(path + ":" + lineAt(error.start) + ": " + message);
  }

  function visit(node, scope, inFunction) {
    const start = node.getStart(file);
    const line = lineAt(start);
    if (ts.isFunctionLike(node) || ts.isClassLike(node)) {
      const name = node.name ?? (ts.isVariableDeclaration(node.parent) ? node.parent.name : undefined);
      const label = name?.getText(file) ?? "<callback>";
      const column = file.getLineAndCharacterOfPosition(start).character + 1;
      scope = scope + "." + label + "@" + line + ":" + column;
      inFunction = ts.isFunctionLike(node);
    }
    if (ts.isStringLiteralLike(node) || ts.isTemplateMiddleOrTemplateTail(node) || node.kind === ts.SyntaxKind.TemplateHead) {
      signals.push({scope, line, end: lineAt(node.end - 1), kind: "string", detail: node.text});
    }
    if (inFunction && ts.isIterationStatement(node, false)) {
      signals.push({scope, line, end: line, kind: "loop"});
    }
    ts.forEachChild(node, child => visit(child, scope, inFunction));
  }

  visit(file, "<module>", false);
  return signals;
}

if (import.meta.main) {
  process.stdout.write(JSON.stringify(scan(process.argv[2], readFileSync(0, "utf8"))));
}
