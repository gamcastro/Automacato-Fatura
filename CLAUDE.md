# CLAUDE.md — conciliador-fatura-ourocard

Este arquivo dá contexto persistente ao Claude Code sobre este projeto de automação e reconciliação financeira. Ele é lido automaticamente no início de toda sessão (extensão do VS Code e CLI).

## Sobre o projeto

Sistema de auditoria e reconciliação automatizada de faturas de cartão de crédito (Ourocard Platinum Estilo Visa do Banco do Brasil). O projeto cruza dados de extratos oficiais em formato `.txt` com telas espelhadas do aplicativo móvel (via Phone Link / Link com o Windows) utilizando visão computacional (OCR) para atribuir com 100% de precisão a responsabilidade de cada compra (George vs. Markleny vs. Cartão Virtual) e exportar um relatório consolidado em Excel.

- **Tecnologias principais**: Python 3.12+, OpenCV, PyTesseract (OCR Engine: `eng`), PyAutoGUI, PyGetWindow, Pandas e OpenPyXL.
- **Fluxo híbrido de validação**:
  1. **Passo 1 (Checklist de Controle)**: Parsing do arquivo `.txt` da fatura do BB para mapear a quantidade exata, datas e valores de todas as transações legítimas (descartando lançamentos de pagamentos, taxas e créditos).
  2. **Passo 2 (Auditoria Computacional via OCR)**: Foco na janela do app Ourocard espelhado. Varredura visual com rolagem estendida (seta up/down por padrão, ver `rolar_teclado_e_processar()` e a constante `MODO_ROLAGEM`) procurando o identificador do cartão (`Ourocard / Platinum / Estilo / Visa`) e capturando o número de 4 dígitos na linha imediatamente inferior para relacionar com o valor lido do `.txt`. Se sobrar item pendente ao chegar numa ponta da lista, o script **alterna a direção da rolagem repetidamente** (ida → volta → ida...) até completar o checklist ou até uma rodada inteira não trazer nenhum item novo (até 3 idas e voltas, ver `max_rodadas` em `executar_fluxo_reconciliacao`) — a mesma função `varrer_lista()` é reaproveitada em ambos os sentidos. Uma rodada sem progresso nenhum é o sinal de que o que sobrou é problema de leitura (ex.: dígito genuinamente ilegível), não de cobertura de tela, e insistir mais não ajuda.
  2c. **Ajuste Fino via Teclado**: Se ainda sobrar pendência depois das rodadas do Passo 2, `ajuste_fino_teclado()` tenta capturar o resto com nudges pequenos de seta para cima/para baixo (3 toques por vez), útil mesmo com `MODO_ROLAGEM = "teclado"` para uma varredura mais fina que os passos padrão de `varrer_lista()`.
  3. **Passo 3 (Exportação Cronológica)**: Ordenação cronológica estrita (da compra mais antiga para a mais recente) e exportação em Excel.

## Padrões estabelecidos (seguir em todo script novo)

- **Prioridade de Leitura Visual**: A busca de cartões deve focar na linha *imediatamente abaixo* da marcação do cartão no app, e não ao redor do preço, para evitar ambiguidades com outros cartões na tela.
- **OCR Dedicado para o Número do Cartão**: Ler o dígito do cartão numa única passada genérica de OCR sobre a tela inteira (`image_to_string`) é a maior fonte de erro de cartão trocado — validado empiricamente: 21 de 47 lançamentos vieram com cartão errado numa execução real, quase todos por dígito mal lido, não por falha de rolagem. A leitura correta usa `image_to_data` para obter as coordenadas (top/bottom) de cada linha, recorta só a linha do número, amplia (`cv2.resize`, ~4x, `INTER_CUBIC`), binariza com Otsu e roda uma segunda passada de OCR restrita a dígitos (`--psm 7 -c tessedit_char_whitelist=0123456789`). Ver `ler_numero_cartao()` em `validador_fatura_mestre.py`.
- **Desempate por Data quando Nome e Valor se Repetem**: Casar uma compra do checklist só pelo valor (`Valor_Str`) é ambíguo quando duas compras do mesmo mês têm o mesmo valor. O nome do estabelecimento ajuda quando os candidatos têm nomes diferentes, mas **não resolve quando o mesmo estabelecimento repete o mesmo valor várias vezes no mês** (ex.: "Indigo R$ 12,00" em 3 datas diferentes — todos os candidatos têm o mesmo nome). Nesse caso o desempate correto é o carimbo de data/hora que o app mostra na linha logo abaixo do número do cartão (ex.: "16/07/2026 às 21:30:19"), comparado com a `Data` do candidato. Ordem de prioridade: data exibida na tela > nome do estabelecimento no bloco lido > primeiro candidato não identificado. Ver `processar_pagina_atual()` em `validador_fatura_mestre.py`.
- **Nova Chance na Mesma Tela antes de Rolar**: Uma leitura de OCR pode falhar por motivo pontual (blur do scroll ainda assentando, ângulo de compressão do vídeo, etc.). Antes de contar uma página como "sem progresso" e seguir rolando, `varrer_lista()` recaptura a mesma tela (sem rolar) uma vez e tenta de novo — barato e reduz pendências residuais sem depender só das passadas de ida/volta.
- **Filtros de Segurança e Exclusão**:
  - Só os finais de cartão cadastrados em `CARTOES_VALIDOS` (`0808`, `1555`, `4025`, `8442`) são aceitos como leitura válida — qualquer outro 4-dígitos lido (`2026` do ano, ou ruído de OCR como `1844`/`1200`/`8155` já observados em execuções reais) é tratado como "Não Identificado" em vez de aceito como se fosse um cartão novo. Isso vale tanto para a leitura dedicada quanto para o fallback de leitura ampla — ver `processar_pagina_atual()`.
  - O valor `0,00` (resíduo de colunas de dólar do `.txt`) e a sigla `BR` devem ser completamente removidos do nome do estabelecimento na saída limpa.
- **Regras de Negócio de Cartões**:
  - Cartão **`0808`** → `George (Principal)`
  - Cartão **`1555`** ou indicador `VIRTUAL` → `Virtual / Internet`
  - **`4025` ou `8442`** (únicos outros cartões cadastrados) → `Markleny`
- **Tratamento de Strings e Codificação**:
  - Abertura do arquivo `.txt` com `encoding='utf-8'` e `errors='ignore'`.
  - Remoção de símbolos monetários (`R$`, `RS`) do bloco de texto limpo para evitar falhas no batimento numérico do OCR.
- **Dependência do Tesseract**: Caminho executável fixado na pasta local de usuário (`C:\Users\<USER>\AppData\Local\Programs\Tesseract-OCR\tesseract.exe`).

## Convenções de Arquivos e Estrutura

- `validador_fatura_mestre.py` — Script principal de reconciliação híbrida.
- `OUROCARD_PLATINUM_ESTILO_VISA-Jul_26.txt` (ou equivalente mensal) — Lista de controle oficial exportada do autoatendimento BB.
- `prints_fatura_validacao/` — Pasta gerada automaticamente para armazenar os relatórios de capturas de tela divididas por páginas (`captura_pag_X.png`).
- `Fatura_Julho_Reconciliada_Perfeita.xlsx` — Relatório final em planilha de auditoria.

## Regras de Execução e Automação

- **Captura da Janela**: O aplicativo Ourocard deve estar aberto no Phone Link com o título visível `Ourocard` e posicionado no topo da lista de lançamentos antes do início do script.
- **Rolagem Principal é por Teclado (seta cima/baixo)**: `MODO_ROLAGEM = "teclado"` no topo de `validador_fatura_mestre.py` controla o mecanismo — confirmado pelo usuário que as setas `up`/`down` rolam a lista sem o "rola demais" por inércia (fling) que o arrasto de mouse tinha. `Page Down` foi testado à parte e confirmado (por vídeo) que **não rola nada** no Phone Link — só as setas simples funcionam. **Nunca clicar em nenhum item da lista**: um clique abre o detalhe do lançamento em vez de rolar a lista.
- **Teclado Exige Foco Reforçado a Cada Tentativa**: `pyautogui.press()` manda a tecla para qualquer janela que estiver em foco no Windows naquele instante — diferente do arrasto de mouse, que clica nas coordenadas da janela e a foca de novo a cada vez. Bug real observado por vídeo: o usuário mexendo no VS Code enquanto o script rodava tirou o foco do Ourocard, as setas pararam de chegar no app, a tela ficou parada de verdade, e o script concluiu "fim da lista" bem cedo — mesmo sem nunca ter rolado além da primeira tela. `garantir_foco_janela()` chama `janela_app.activate()` antes de cada tentativa de tecla (em `rolar_teclado_e_processar()` e `ajuste_fino_teclado()`) para evitar isso.
- **Arrasto de Mouse Preservado como Alternativa (`MODO_ROLAGEM = "mouse"`)**: Todo o código do arrasto de mouse (`rolar_e_processar_pagina()`, `arrastar_mouse_relativo()`) continua no arquivo, só não é usado por padrão. Trocar a constante `MODO_ROLAGEM` para `"mouse"` reverte para ele caso a rolagem por teclado pare de funcionar (ex.: o app mudar de versão). Notas históricas do arrasto de mouse (podem ficar desatualizadas se `MODO_ROLAGEM` não for `"mouse"`):
  - `pyautogui.moveTo()` no Windows usa `SetCursorPos()` (teleporte absoluto), que não gera os deltas de movimento relativo que o Phone Link espera para reconhecer um arrasto — por isso `arrastar_mouse_relativo()` usa `mouse_event(MOUSEEVENTF_MOVE, ...)` em pequenos passos.
  - Soltar o botão dispara um "fling" de inércia que pode continuar rolando por 3-4s — `rolar_e_processar_pagina()` captura a tela **antes** de soltar para não ser afetado por isso.
  - A aceleração de ponteiro do Windows amplia essa sequência de `mouse_event` de forma não-linear — `executar_fluxo_reconciliacao()` desativa via `SPI_SETMOUSE` e restaura ao final (`try`/`finally`), independente do `MODO_ROLAGEM` ativo.

- **Critério de "Fim da Lista" é só Contagem de Páginas sem Item Novo (histórico da comparação de pixels)**: Duas fases desse critério:
  1. Primeiro descobrimos (analisando os prints em `prints_fatura_validacao/`) que rolar por território já reconciliado numa rodada anterior também produz "nenhum item novo" a cada página, igual a rolar contra um limite físico da lista — só que a tela está genuinamente mudando. A correção então foi exigir tela parada (diferença de pixels abaixo de um limiar) *e* nenhum item novo.
  2. Isso funcionava para o arrasto de mouse (passos grandes, ~60% da altura da janela — qualquer progresso gerava uma diferença de pixels bem acima do limiar), mas quebrou com a rolagem por teclado: cada toque de seta move pouco, então a diferença de pixels fica pequena mesmo com progresso genuíno — a comparação de pixels declarava "fim da lista" cedo demais (visto no vídeo: parava depois de só 2 itens, bem antes do fim real).
  3. `varrer_lista()` agora usa só a contagem de páginas seguidas sem item novo (parâmetro `paciencia`, padrão 5) — mesmo critério simples e já validado do `ajuste_fino_teclado()`. Não depende mais de comparação de pixels em nenhum dos dois mecanismos de rolagem. `max_paginas` também subiu para 60 (de 35), já que o teclado avança menos por passo que o arrasto.

- **Sessão do Ourocard Expira em Execuções Longas**: Com várias rodadas de ida/volta (até 3 idas e voltas completas), a execução pode demorar o suficiente para a sessão do app cair no meio do caminho — o app volta para uma tela de erro/sessão expirada, e sem detecção o script continuava rolando e tentando ler OCR dessa tela vazia até esgotar as rodadas. `processar_pagina_atual()` agora verifica se a tela ainda tem algum dos textos de referência do app (`MARCADORES_TELA_FATURA`: "Ourocard", "Platinum", "Fatura", etc.); se sumirem por 2 páginas seguidas, `SessaoExpiradaError` é levantada e o Passo 2 para imediatamente (exportando o que já foi reconciliado até ali) em vez de insistir no vazio. Se isso disparar com frequência, considerar reduzir `max_rodadas`/`max_paginas` para caber numa janela de sessão mais curta.

## Problemas conhecidos / Diagnósticos em aberto

- **Valores Duplicados**: Mitigado pelo desempate por nome do estabelecimento (ver "Padrões estabelecidos" acima), mas ainda pode falhar se o mesmo estabelecimento e valor se repetirem *e* o nome não aparecer no bloco de texto lido daquela tela (ex.: OCR cortou o nome). Nesse caso residual, ainda pode validar a primeira ocorrência do checklist que corresponder ao valor.
- **Velocidade de Rolagem do Phone Link**: Em conexões Wi-Fi lentas, os renders do app Ourocard podem atrasar; aumentar o `time.sleep()` se houver lançamentos marcados como `Pendente Visão OCR`.
- **Fim da Lista e "Bounce" Elástico**: Ao arrastar além do último item, o app faz um efeito elástico de retorno (a tela rola um pouco e volta), então continuar tentando arrastar no fim da lista gera oscilação (rola para cima, depois para baixo) sem nunca revelar itens novos — os que ficaram como `Pendente Visão OCR` até esse ponto não serão mais capturados, pois não há mais conteúdo para rolar. **Comparar hash/pixels da captura não detecta isso de forma confiável**: por causa do bounce, a tela nunca fica byte-a-byte idêntica à anterior mesmo sem progresso real (confirmado por análise de vídeo — variação de pixels a cada segundo mesmo com o conteúdo visualmente parado por 20s+). O sinal confiável é a ausência de **itens novos reconciliados**: o script encerra o Passo 2 após 3 páginas seguidas sem nenhum match novo (ver `paginas_sem_progresso` em `validador_fatura_mestre.py`), em vez de insistir até o `max_paginas`.

## Ao criar ou editar scripts

- Preservar a estratégia do **Passo 1 (TXT como Lista de Controle)**: Nunca depender apenas do OCR para saber *quantas* compras existem, o `.txt` é a fonte da verdade para o total de lançamentos.
- Manter a conversão de datas usando `pd.to_datetime` para assegurar que a ordenação cronológica do relatório nunca seja corrompida.
- Garantir clareza nos logs do terminal durante a execução do loop para acompanhamento em tempo real pelo usuário.