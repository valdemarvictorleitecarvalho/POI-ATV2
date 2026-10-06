# Aplicação e características

A aplicação é uma API HTTP que simula a persistência durável de dados, como um banco de dados gravando seu log de transações. Cada requisição a /io cria um arquivo, grava nele uma quantidade de dados em blocos e chama fsync após cada bloco, o que obriga o sistema operacional a levar os dados até o disco antes de continuar. No fim, o arquivo é apagado para o disco não encher.

Os dados gravados são gerados uma única vez na subida de cada processo e reutilizados, então a requisição não gasta CPU produzindo conteúdo. O fsync impede que o cache de páginas absorva a escrita: aumentar a RAM da máquina não esconde o custo do disco. O que limita a requisição é a espera pelo dispositivo, e durante essa espera a CPU fica livre.

As operações de arquivo do Node rodam no pool de threads do libuv (4 threads por processo, por padrão), sem bloquear o event loop. Um mesmo processo consegue manter várias gravações em andamento enquanto continua aceitando requisições. Quando a demanda cresce, as gravações disputam o disco: a vazão em bytes por segundo se aproxima da capacidade do dispositivo e o tempo de resposta passa a crescer com a fila de IO, enquanto a utilização de CPU continua baixa e o iowait sobe.

Na subida, o processo principal cria um worker Node para cada CPU que o sistema operacional vê, como no backend CPU-bound. Dá para fixar outra quantidade com WEB_CONCURRENCY, ou usar um único processo com CLUSTER=false.

Foram implementadas três rotas:

GET / descreve a aplicação e os limites aceitos.
GET /health só confirma que o processo está no ar, sem IO.
GET /io?sizeKb=256&syncs=4 faz a gravação. sizeKb é o total gravado (inteiro entre 4 e 8192, padrão 256) e syncs é a quantidade de blocos, cada um seguido de fsync (inteiro entre 1 e 64, padrão 4).

A resposta de /io traz os bytes gravados, o tempo total no worker (elapsedMs), o tempo esperando fsync (syncMs) e o pid de quem atendeu.

Para os experimentos, a demanda entra por HTTP, de fora da máquina virtual. O tempo de resposta e as requisições por segundo saem da ferramenta de carga. A diferença entre syncMs e elapsedMs mostra que quase todo o tempo é espera do disco. O provisionamento que afeta essa aplicação é a capacidade de IO, que no VirtualBox pode ser limitada com grupos de banda (VBoxManage bandwidthctl).
