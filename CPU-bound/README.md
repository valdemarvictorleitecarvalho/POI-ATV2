# Backend CPU-bound

API HTTP em NestJS. A rota de trabalho conta quantos números primos existem até um limite, por divisão tentativa. Essa conta usa CPU e mantém memória extra constante por requisição.

Há um processo Node para cada CPU visível. Aumentar as vCPUs da máquina aumenta a quantidade de requisições que podem ser calculadas ao mesmo tempo.

## Executar

```bash
npm install
npm run build
npm run start:prod
```

Em desenvolvimento: `npm run start:dev`.

A aplicação escuta em `0.0.0.0:3000`.

## Rotas

- `GET /` — o que a aplicação faz e os limites aceitos
- `GET /health` — processo no ar, sem carga de CPU
- `GET /compute?limit=1000000` — contagem de primos; `limit` inteiro de 1000 a 5000000 (padrão 1000000)

A resposta de `/compute` traz a quantidade de primos, o tempo de cálculo no processo (`elapsedMs`) e o `pid` de quem atendeu.

## Variáveis

- `PORT` — porta (padrão 3000)
- `CLUSTER=false` — um único processo
- `WEB_CONCURRENCY` — quantidade de workers, se for diferente do número de CPUs
