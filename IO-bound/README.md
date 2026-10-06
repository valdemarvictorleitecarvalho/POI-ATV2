# Backend IO-bound

API HTTP em NestJS. A rota de trabalho grava um arquivo em disco em blocos, chamando `fsync` após cada bloco, e apaga o arquivo no fim. O tempo de cada requisição é dominado pela espera do disco; a CPU fica quase ociosa.

Há um processo Node para cada CPU visível, como no backend CPU-bound. Aqui, mais vCPUs não aumentam a vazão: o limite é o disco.

## Executar

```bash
npm install
npm run build
npm run start:prod
```

Em desenvolvimento: `npm run start:dev`.

A aplicação escuta em `0.0.0.0:3000` e grava em `./data` (criada na subida).

## Rotas

- `GET /` — o que a aplicação faz e os limites aceitos
- `GET /health` — processo no ar, sem IO
- `GET /io?sizeKb=256&syncs=4` — grava `sizeKb` KB divididos em `syncs` blocos, com `fsync` após cada um; `sizeKb` de 4 a 8192 (padrão 256), `syncs` de 1 a 64 (padrão 4)

A resposta de `/io` traz os bytes gravados, o tempo total no processo (`elapsedMs`), o tempo esperando `fsync` (`syncMs`) e o `pid` de quem atendeu.

## Variáveis

- `PORT` — porta (padrão 3000)
- `DATA_DIR` — diretório dos arquivos temporários (padrão `./data`); precisa estar no disco da VM, não em tmpfs
- `CLUSTER=false` — um único processo
- `WEB_CONCURRENCY` — quantidade de workers, se for diferente do número de CPUs
- `UV_THREADPOOL_SIZE` — threads de IO de arquivo por processo (padrão do Node: 4)
