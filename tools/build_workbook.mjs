import fs from "node:fs/promises";
import path from "node:path";
const { SpreadsheetFile, Workbook } = await import(
  process.env.MANNUL_ARTIFACT_MODULE_URL || "@oai/artifact-tool"
);

const [payloadFile, outputFile, diagnosticsDir] = process.argv.slice(2);
if (!payloadFile || !outputFile) {
  throw new Error("用法: node build_workbook.mjs <payload.json> <output.xlsx> [diagnostics-dir]");
}
const payload = JSON.parse(await fs.readFile(payloadFile, "utf8"));
const wb = Workbook.create();

const colors = {
  navy: "#17365D", blue: "#2F75B5", lightBlue: "#D9EAF7", green: "#E2F0D9",
  red: "#FCE4D6", yellow: "#FFF2CC", gray: "#E7E6E6", white: "#FFFFFF", text: "#1F2937",
};

function colName(n) {
  let s = "";
  while (n > 0) { n--; s = String.fromCharCode(65 + (n % 26)) + s; n = Math.floor(n / 26); }
  return s;
}

function matrix(rows, headers) {
  return [headers, ...rows.map((row) => headers.map((h) => row[h] ?? ""))];
}

function styleTable(sheet, rowCount, colCount, widths = {}) {
  const lastCol = colName(colCount);
  const header = sheet.getRange(`A1:${lastCol}1`);
  header.format = {
    fill: colors.navy, font: { bold: true, color: colors.white },
    verticalAlignment: "center", wrapText: true,
    borders: { preset: "outside", style: "thin", color: colors.navy },
  };
  header.format.rowHeight = 32;
  if (rowCount > 1) {
    const body = sheet.getRange(`A2:${lastCol}${rowCount}`);
    body.format = { verticalAlignment: "top", wrapText: false };
    body.format.borders = { insideHorizontal: { style: "thin", color: "#E5E7EB" } };
  }
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
  for (let i = 1; i <= colCount; i++) {
    sheet.getRangeByIndexes(0, i - 1, Math.max(rowCount, 1), 1).format.columnWidth = widths[i] ?? 15;
  }
}

function addRecordSheet(name, rows) {
  const sheet = wb.worksheets.add(name);
  const data = matrix(rows, payload.record_headers);
  sheet.getRangeByIndexes(0, 0, data.length, payload.record_headers.length).values = data;
  const widths = {
    1: 23, 2: 11, 3: 12, 4: 11, 5: 12, 6: 24, 7: 36, 8: 42, 9: 44, 10: 26,
    11: 9, 12: 24, 13: 58, 14: 34, 15: 34, 16: 24, 17: 23, 18: 16, 19: 10,
    20: 28, 21: 11, 22: 13, 23: 42, 24: 34, 25: 34, 26: 24, 27: 34,
  };
  styleTable(sheet, data.length, payload.record_headers.length, widths);
  if (data.length > 1) {
    sheet.getRange(`D2:D${data.length}`).format.numberFormat = "0.0%";
    sheet.getRange(`K2:K${data.length}`).format.numberFormat = "0";
    sheet.getRange(`U2:V${data.length}`).format.numberFormat = "#,##0";
    const decisionRange = sheet.getRange(`B2:B${data.length}`);
    decisionRange.conditionalFormats.addCustom(`=$B2="相关"`, { fill: colors.green });
    decisionRange.conditionalFormats.addCustom(`=$B2="不相关"`, { fill: colors.red });
    decisionRange.conditionalFormats.addCustom(`=$B2="存疑"`, { fill: colors.yellow });
    const tableName = `T_${name.replace(/[^A-Za-z0-9]/g, "") || Math.random().toString(36).slice(2)}`;
    const table = sheet.tables.add(`A1:${colName(payload.record_headers.length)}${data.length}`, true, tableName);
    table.style = "TableStyleMedium2";
    table.showFilterButton = true;
  }
  return sheet;
}

const allRows = payload.records;
addRecordSheet("筛选总表", allRows);
addRecordSheet("相关文献", allRows.filter((r) => r["最终分类"] === "相关"));
addRecordSheet("不相关文献", allRows.filter((r) => r["最终分类"] === "不相关"));
addRecordSheet("人工复核记录", allRows.filter((r) => r["AI初始分类"] === "存疑"));

const dupSheet = wb.worksheets.add("去重日志");
const dupHeaders = ["重复记录ID", "保留记录ID", "去重依据", "重复来源", "保留来源"];
const dupRows = payload.duplicates.map((r) => [r.duplicate_record_id, r.kept_record_id, r.reason, r.duplicate_source, r.kept_source]);
dupSheet.getRangeByIndexes(0, 0, dupRows.length + 1, dupHeaders.length).values = [dupHeaders, ...dupRows];
styleTable(dupSheet, dupRows.length + 1, dupHeaders.length, { 1: 26, 2: 26, 3: 16, 4: 42, 5: 42 });

const failSheet = wb.worksheets.add("API失败记录");
const failHeaders = ["记录ID", "题名", "错误"];
const failRows = payload.failures.map((r) => failHeaders.map((h) => r[h] ?? ""));
failSheet.getRangeByIndexes(0, 0, failRows.length + 1, failHeaders.length).values = [failHeaders, ...failRows];
styleTable(failSheet, failRows.length + 1, failHeaders.length, { 1: 26, 2: 48, 3: 64 });

const info = wb.worksheets.add("运行信息");
info.getRange("A1:B1").values = [["运行参数", "值"]];
const infoRows = Object.entries(payload.run_info);
info.getRangeByIndexes(1, 0, infoRows.length, 2).values = infoRows;
styleTable(info, infoRows.length + 1, 2, { 1: 26, 2: 88 });
info.getRange("A2:A20").format.font = { bold: true, color: colors.text };
info.getRange("B2:B20").format.wrapText = true;

const inspected = await wb.inspect({
  kind: "table", range: "运行信息!A1:B20", include: "values,formulas",
  tableMaxRows: 20, tableMaxCols: 4, maxChars: 5000,
});
const recordColumns = await wb.inspect({
  kind: "table", range: "筛选总表!A1:I15", include: "values,formulas",
  tableMaxRows: 15, tableMaxCols: 9, maxChars: 8000,
});
const errors = await wb.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan", maxChars: 3000,
});
if (diagnosticsDir) {
  await fs.mkdir(diagnosticsDir, { recursive: true });
  await fs.writeFile(
    path.join(diagnosticsDir, "workbook_validation.txt"),
    `${inspected.ndjson}\n${recordColumns.ndjson}\n${errors.ndjson}`,
    "utf8",
  );
  const previewRanges = {
    "筛选总表": "A1:AA15", "相关文献": "A1:AA15", "不相关文献": "A1:AA15", "人工复核记录": "A1:AA15",
    "去重日志": "A1:E15", "API失败记录": "A1:C15", "运行信息": "A1:B15",
  };
  for (const [name, range] of Object.entries(previewRanges)) {
    const preview = await wb.render({ sheetName: name, range, scale: 0.8, format: "png" });
    await fs.writeFile(path.join(diagnosticsDir, `preview_${name}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}
const exported = await SpreadsheetFile.exportXlsx(wb);
await exported.save(outputFile);
