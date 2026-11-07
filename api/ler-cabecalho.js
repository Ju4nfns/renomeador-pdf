// Função serverless da Vercel: lê o nº da NFe e a data no topo de um espelho de recebimento escaneado.
// A chave da Anthropic fica só aqui no servidor (variável de ambiente), nunca no navegador.
const PROMPT = 'A imagem é o topo de um espelho de recebimento de materiais. Leia o número da coluna "NFe" (ou "NOTA FISCAL") e a "DATA RECEBIMENTO". Responda só com JSON no formato {"nfe":"somente dígitos","data":"dd/mm/aaaa"}, usando "" no que não conseguir ler.';

module.exports = async (req, res) => {
  const senha = process.env.APP_PASSWORD, key = process.env.ANTHROPIC_API_KEY;
  // Abrir o endereço no navegador mostra se as variáveis estão configuradas (só sim/não, nunca os valores).
  if (req.method === 'GET') return res.status(200).json({ ok: true, ANTHROPIC_API_KEY: !!key, APP_PASSWORD: !!senha });
  if (req.method !== 'POST') return res.status(405).json({ error: 'método não permitido' });
  const falta = [!key && 'ANTHROPIC_API_KEY', !senha && 'APP_PASSWORD'].filter(Boolean);
  if (falta.length) return res.status(500).json({ error: 'falta configurar na Vercel: ' + falta.join(' e ') });
  if (req.headers['x-app-password'] !== senha) return res.status(401).json({ error: 'senha incorreta' });
  const image = req.body && req.body.image;
  if (typeof image !== 'string' || !image || image.length > 3000000) return res.status(400).json({ error: 'imagem inválida ou grande demais' });
  try {
    const r = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01' },
      body: JSON.stringify({
        model: process.env.ANTHROPIC_MODEL || 'claude-haiku-5-5',
        max_tokens: 100,
        messages: [{ role: 'user', content: [
          { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data: image } },
          { type: 'text', text: PROMPT }
        ] }]
      })
    });
    if (r.status === 429) return res.status(429).json({ error: 'limite de uso da API' });
    if (!r.ok) {
      const detalhe = (await r.text().catch(() => '')).replace(/\s+/g, ' ').slice(0, 160);
      return res.status(502).json({ error: 'a API da Anthropic respondeu ' + r.status + ': ' + detalhe });
    }
    const j = await r.json();
    const txt = (j.content || []).map(c => c.text || '').join('');
    let out = {};
    try { const m = txt.match(/\{[\s\S]*\}/); out = m ? JSON.parse(m[0]) : {}; } catch (e) {}
    return res.status(200).json({ nfe: String(out.nfe || ''), data: String(out.data || '') });
  } catch (e) {
    console.error(e);
    return res.status(500).json({ error: 'erro interno: ' + String((e && e.message) || e).slice(0, 150) });
  }
};
