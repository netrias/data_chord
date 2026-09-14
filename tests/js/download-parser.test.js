import assert from 'node:assert/strict';
import test from 'node:test';
import AdmZip from 'adm-zip';

import { parseDownloadedCsvTable, parseDownloadedTabular } from '../e2e/utils.mjs';

test('download proof reads quoted multiline cells as one record', async () => {
  // Given: the real ZIP shape contains commas, quotes, Unicode, and a cell newline.
  const zip = new AdmZip();
  zip.addFile('results.csv', Buffer.from('case_id,diagnosis,notes\nC01,"Line one\nLine two","site, \"\"quoted\"\""\nC02,NaN,café\n'));
  const mockResponse = { body: async () => zip.toBuffer() };

  // When: the browser-test download reader inspects that ZIP.
  const table = await parseDownloadedCsvTable(mockResponse);

  // Then: the complete table retains cells and records, without splitting the newline.
  assert.deepEqual(table, {
    headers: ['case_id', 'diagnosis', 'notes'],
    rows: [['C01', 'Line one\nLine two', 'site, "quoted"'], ['C02', 'NaN', 'café']],
  });
});

test('download proof retains quoted CRLF and a final empty field without an ending newline', async () => {
  // Given: the CSV uses CRLF records and also has CRLF inside a quoted cell.
  const zip = new AdmZip();
  zip.addFile('results.csv', Buffer.from('case_id,diagnosis,notes\r\nC01,"first\r\nsecond",\r\nC02,Diabetes,'));
  const mockResponse = { body: async () => zip.toBuffer() };

  // When: the download reader reads the full CSV.
  const table = await parseDownloadedCsvTable(mockResponse);

  // Then: record endings do not change cell contents or remove empty final cells.
  assert.deepEqual(table, {
    headers: ['case_id', 'diagnosis', 'notes'],
    rows: [['C01', 'first\r\nsecond', ''], ['C02', 'Diabetes', '']],
  });
});

test('download proof distinguishes quoted tabs from TSV field separators', async () => {
  // Given: the TSV has a tab inside a quoted cell and a final empty field.
  const zip = new AdmZip();
  zip.addFile('results.tsv', Buffer.from('case_id\tdiagnosis\tnotes\nC01\t"first\tsecond"\t\n'));
  const mockResponse = { body: async () => zip.toBuffer() };

  // When: the download reader reads the TSV entry.
  const rows = await parseDownloadedTabular(mockResponse, '.tsv', '\t');

  // Then: the complete table retains the quoted tab in its cell.
  assert.deepEqual(rows, [{ case_id: 'C01', diagnosis: 'first\tsecond', notes: '' }]);
});
