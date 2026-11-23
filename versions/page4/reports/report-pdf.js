const asset = name => new URL('./assets/report/' + name, import.meta.url).href;
let resources;

async function fontBytes(name) {
  const response = await fetch(asset(name));
  if (!response.ok) throw new Error('보고서 글꼴을 불러오지 못했습니다.');
  return new Uint8Array(await response.arrayBuffer());
}

function loadEngine() {
  if (window.pdfMake) return Promise.resolve(window.pdfMake);
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = asset('pdfmake.min.js');
    script.onload = () => resolve(window.pdfMake);
    script.onerror = () => { script.remove(); reject(new Error('PDF 생성 도구를 불러오지 못했습니다.')); };
    document.head.append(script);
  });
}

// Provide wrap opportunities for long, unspaced filenames and user-entered descriptions.
const wrap = value => String(value).replace(/\S{45,}/gu, word => Array.from(word).reduce((text, char, i) =>
  text + (i && i % 28 === 0 ? '\u200b' : '') + char, ''));
const field = (label, value, wrapLongWords = true) => ({
  text: [{ text: label + ': ', bold: true }, wrapLongWords ? wrap(value) : value], margin: [0, 0, 0, 12]
});

function evidenceCard(item, options, formatDate) {
  const lines = [{ text: '증거 파일: ' + wrap(item.filename || item.name) + ' (ID: ' + wrap(item.id) + ')', bold: true, fontSize: 11, margin: [0, 2, 0, 10] }];
  if (options.includes('basic')) {
    if (item.name && item.filename && item.name !== item.filename) lines.push(field('증거 이름', item.name));
    lines.push(field('파일 크기', Number.isFinite(item.size) ? item.size + ' Bytes' : '미기록'));
  }
  if (item.registeredAt && (options.includes('basic') || options.includes('custody'))) {
    lines.push(field('등록 일시', formatDate(item.registeredAt) + ' (KST)'), field('등록자', item.registeredBy || '미기록'));
  }
  if (options.includes('custody')) {
    const snapshot = item.custodySnapshot;
    const reference = typeof snapshot === 'string' ? snapshot
      : [snapshot?.sequenceNo, snapshot?.eventHash].filter(value => value != null && value !== '').join(' · ');
    // Keep the event hash intact when copied from the PDF.
    lines.push({ ...field('관리 이력 기준점', reference || '미기록', false), fontSize: 8 });
  }
  if (options.includes('metadata')) lines.push(field('수집 출처', item.source || '미기록'), field('파일 형식', item.mime || item.type || '미기록'));
  if (options.includes('results')) {
    lines.push(field('검증 상태', item.status || '미검증'));
    if (item.lastVerification?.reason) lines.push(field('검증 설명', item.lastVerification.reason));
  }
  if (item.lastVerification?.completedAt && (options.includes('results') || options.includes('custody'))) {
    lines.push(field('마지막 검증', formatDate(item.lastVerification.completedAt) + ' (KST)'),
      field('검증자', item.lastVerification.performedBy || '미기록'));
  }
  if (options.includes('hash')) {
    lines.push({ text: '무결성 해시 (SHA-256)', bold: true, margin: [0, 3, 0, 7] },
      { table: { widths: ['*'], body: [[{ text: item.sha256 || '미기록', fillColor: '#f4f4f4', fontSize: 8, margin: [4, 4] }]] }, layout: 'noBorders' });
    if (item.lastVerification?.currentHash) lines.push({ text: '검증 SHA-256: ' + item.lastVerification.currentHash, fontSize: 8, margin: [0, 6, 0, 0] });
  }
  return { margin: [0, 0, 0, 12], table: { dontBreakRows: true, widths: ['*'], body: [[{ stack: lines, margin: [6, 8, 6, 6] }]] },
    layout: { hLineWidth: () => 0.6, vLineWidth: () => 0.6, hLineColor: () => '#cccccc', vLineColor: () => '#cccccc' } };
}

export async function createReportPdf(data, items, formatDate) {
  if (!resources) resources = Promise.all([loadEngine(), fontBytes('NanumGothic-Regular.ttf'), fontBytes('NanumGothic-Bold.ttf')])
    .catch(error => { resources = null; throw error; });
  const [engine, regular, bold] = await resources;
  const id = data.id || '생성 시 발급';
  const content = [{ text: wrap(data.title), bold: true, fontSize: 18, alignment: 'center', margin: [0, 0, 0, 16] }];
  for (const subtitle of [data.project, data.description].filter(Boolean)) {
    content.push({ text: wrap(subtitle), alignment: 'center', margin: [0, 0, 0, 8] });
  }
  content.push({ canvas: [{ type: 'line', x1: 0, y1: 0, x2: 471, y2: 0, lineWidth: 1.5 }], margin: [0, 8, 0, 13] },
    { ...field('보고서 ID', id), color: '#c93838' }, field('작성자', data.author || '미기록'),
    field('생성 일시', data.created ? data.created + (data.legacyTime ? ' (시간대 미기록)' : ' (KST)') : '생성 시 기록'),
    { text: '수집된 증거 목록', bold: true, fontSize: 14, margin: [0, 6, 0, 13] },
    ...items.map(item => evidenceCard(item, data.options, formatDate)));
  if (!items.length) content.push({ text: data.id ? '생성 당시 저장된 증거 상세 정보가 없습니다.' : '선택한 증거가 없습니다.' });
  const definition = { info: { title: data.title, author: data.author || '', subject: data.created || '' },
    pageSize: 'A4', pageMargins: [62, 63, 62, 58], defaultStyle: { font: 'Nanum', fontSize: 9.5, lineHeight: 1.15, color: '#303030' },
    content, footer: (page, count) => ({ text: 'EVIDENCE ' + (data.id || '미리보기') + (count > 1 ? '   ' + page + ' / ' + count : ''), alignment: 'center', fontSize: 9, margin: [0, 18, 0, 0] }) };
  const pdf = engine.createPdf(definition, {}, { Nanum: { normal: 'regular.ttf', bold: 'bold.ttf' } }, { 'regular.ttf': regular, 'bold.ttf': bold });
  const stream = pdf.getStream();
  return new Promise((resolve, reject) => {
    const chunks = [];
    stream.on('data', chunk => chunks.push(chunk));
    stream.on('error', reject);
    stream.on('end', () => resolve(new Blob(chunks, { type: 'application/pdf' })));
    stream.end();
  });
}
