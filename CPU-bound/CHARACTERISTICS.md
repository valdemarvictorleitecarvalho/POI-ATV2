# Aplicação e características

A aplicação é uma API HTTP que visa contar quantos números primos existem até um limite informado pelo usuário. O método escolhido foi a divisão tentativa, que para cada ímpar até o limite, o processo testa divisores até a raiz quadrada do mesmo. O estado disso são algumas variáveis numéricas, então o consumo de memória por requisição se mantém praticamente o mesmo quando o limite cresce. O que cresce então, é o tempo de CPU.

O cálculo roda de forma síncrona dentro do worker que recebeu a requisição e ocupa esse worker até finalizar tudo. Com mais requisições simultâneas do que workers, as que chegam depois esperam na fila do processo. O tempo de resposta observado pelo cliente passa a incluir esse tempo de espera, além do tempo de cálculo. A vazão deixa de subir quando todos os workers estão ocupados.

Na subida, o processo principal cria um worker Node para cada CPU que o sistema operacional vê. Na máquina virtual, aumentar as vCPUs aumenta a quantidade de cálculos que podem ocorrer ao mesmo tempo. Dá para fixar outra quantidade com WEB_CONCURRENCY, ou usar um único processo com CLUSTER=false.

Foram implementadas três rotas:

GET /descreve a aplicação e os limites aceitos.
GET /health só confirma que o processo está no ar, sem carga de CPU.
GET /compute?limit=1000000 faz a contagem. O padrão é 1.000.000 e o valor precisa ser um inteiro entre 1.000 e 5.000.000.

A resposta de /compute traz a quantidade de primos, o tempo de cálculo naquele worker (elapsedMs) e o pid de quem atendeu.

Para os experimentos, a demanda entra por HTTP, de fora da máquina virtual. O tempo de resposta e as requisições por segundo saem da ferramenta de carga. O elapsedMs separa o tempo de cálculo da espera na fila. 
