# Renomeador de PDF (NFe)

Renomeia PDFs pelo número da NFe do cabeçalho. Roda no navegador; para PDFs escaneados, uma função
no servidor (`api/ler-cabecalho.js`) lê só a imagem do cabeçalho com a API da Anthropic.

## Estrutura
- `public/index.html`: o app (página única)
- `api/ler-cabecalho.js`: função da Vercel que guarda a chave da API (a chave nunca vai para o navegador)
- `desktop/renomeador_em_massa.py`: programa para o computador, para milhares de arquivos (NÃO roda na Vercel)

## Publicar
1. **GitHub:** crie um repositório novo, clique em *Add file → Upload files*, arraste o conteúdo desta pasta
   (mantendo as pastas `public`, `api` e `desktop`) e faça o commit.
2. **Vercel:** *Add New → Project*, escolha o repositório e clique em *Deploy*. Não precisa de build.
3. Na Vercel, em *Settings → Environment Variables*, crie:
   - `ANTHROPIC_API_KEY`: chave criada em console.anthropic.com (a API é cobrada à parte da assinatura do Claude)
   - `APP_PASSWORD`: uma senha sua; sem ela qualquer pessoa com o link poderia gastar sua chave
   - `ANTHROPIC_MODEL` (opcional): troque o modelo se quiser (padrão: `claude-haiku-5-5`)
4. Faça *Redeploy* para as variáveis valerem. Ao abrir o app, ele pede a senha na primeira leitura.

## Limites
A versão web guarda os arquivos na memória do navegador e monta o .zip na memória: use para dezenas ou
poucas centenas de PDFs por vez. Para milhares, use `desktop/renomeador_em_massa.py`.
PDFs com texto são lidos só no navegador. Em escaneados, só a imagem do cabeçalho da 1ª página é enviada.
