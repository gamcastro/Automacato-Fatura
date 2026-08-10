import pygetwindow as gw
import pyautogui
import time
import os
import cv2
import pytesseract
import re
import pandas as pd
import ctypes
from datetime import datetime

# Configura o caminho do Tesseract na sua área de usuário do Windows 11
pytesseract.pytesseract.tesseract_cmd = r'D:\Usuarios\029342881104\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'

MOUSEEVENTF_MOVE = 0x0001
SPI_GETMOUSE = 0x0003
SPI_SETMOUSE = 0x0004

# Únicos finais de cartão que de fato existem nesta fatura. Qualquer leitura de OCR fora
# deste conjunto (ex.: "1844", "1200", "8155") é ruído de leitura, não um cartão novo —
# rejeitar em vez de aceitar evita reconciliar um item com um cartão que não existe.
CARTOES_VALIDOS = {"0808", "1555", "4025", "8442"}

# "teclado" (seta up/down) ou "mouse" (arrasto). Confirmado pelo usuário que teclado rola
# sem o "rola demais" do fling do arrasto. Trocar aqui para reverter caso precise — o
# código de arrasto de mouse continua intacto em rolar_e_processar_pagina().
MODO_ROLAGEM = "teclado"

def obter_configuracao_mouse():
    valores = (ctypes.c_int * 3)()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETMOUSE, 0, ctypes.byref(valores), 0)
    return list(valores)

def definir_configuracao_mouse(valores):
    array = (ctypes.c_int * 3)(*valores)
    ctypes.windll.user32.SystemParametersInfoW(SPI_SETMOUSE, 0, ctypes.byref(array), 0)

def arrastar_mouse_relativo(distancia_y, passos=40, atraso_passo=0.02):
    """ Move o mouse em pequenos incrementos relativos (mouse_event), pois o Phone Link
    não reconhece o teleporte absoluto do SetCursorPos usado por pyautogui.moveTo()
    como um gesto de arrasto de tela.
    Requer a aceleração de ponteiro do Windows desativada (ver desativar_aceleracao_mouse
    em executar_fluxo_reconciliacao) — com aceleração ativa, essa sequência de micro-movimentos
    rápidos pode ser amplificada de forma não-linear pelo SO, fazendo a rolagem real no app
    passar do ponto pretendido de forma inconsistente. """
    passo_y = int(distancia_y / passos)
    for _ in range(passos):
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_MOVE, 0, passo_y, 0, 0)
        time.sleep(atraso_passo)

def ler_numero_cartao(imagem_cinza, top, bottom, margem=4, escala=4):
    """ Recorta e amplia só a linha com o número do cartão para uma segunda leitura de
    OCR dedicada e restrita a dígitos. Ler o número junto com o resto da tela numa única
    passada genérica é uma fonte de erro de cartão trocado, já que o dígito é pequeno
    perto do texto normal do app.
    Binariza ANTES de ampliar: o Otsu precisa da distribuição de tons original do recorte
    pequeno — aplicá-lo depois de um resize cúbico borra a transição preto/branco e piora
    a leitura. """
    y0 = max(0, top - margem)
    y1 = bottom + margem
    recorte = imagem_cinza[y0:y1, :]
    if recorte.size == 0:
        return None
    _, recorte_bin = cv2.threshold(recorte, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    recorte_grande = cv2.resize(recorte_bin, None, fx=escala, fy=escala, interpolation=cv2.INTER_CUBIC)
    texto = pytesseract.image_to_string(
        recorte_grande, config='--psm 7 -c tessedit_char_whitelist=0123456789'
    )
    match = re.search(r'\d{4}', texto)
    return match.group(0) if match else None

class SessaoExpiradaError(Exception):
    """ A tela parou de parecer a lista de lançamentos do Ourocard (sem nenhum dos textos
    de referência do app) por páginas seguidas — sinal de que a sessão expirou (o app volta
    para uma tela de erro/login) e não adianta continuar rolando/lendo OCR no vazio. """
    pass

MARCADORES_TELA_FATURA = ("OUROCARD", "PLATINUM", "ESTILO", "VISA", "FATURA", "COMPARTILHAR")

def pagina_parece_fatura(linhas_ocr):
    texto_completo = " ".join(l["texto"] for l in linhas_ocr).upper()
    return any(marcador in texto_completo for marcador in MARCADORES_TELA_FATURA)

def contar_reconciliadas(lista_controle):
    return sum(1 for c in lista_controle if c["Cartao"] != "Não Identificado")

def processar_pagina_atual(janela_app, pasta_prints, pag_num, lista_controle):
    """ Captura a tela atual do app, roda o OCR e tenta reconciliar os itens visíveis
    com o checklist. Usado tanto na varredura para frente quanto na volta.
    Retorna (imagem em escala de cinza, tela parece a fatura?): a imagem serve para o
    chamador comparar com a página anterior e distinguir "travado de verdade" de "rolando
    por território já coberto"; o booleano sinaliza se a tela ainda parece a lista de
    lançamentos (usado para detectar sessão expirada). """
    nome_print = os.path.join(pasta_prints, f"captura_pag_{pag_num}.png")

    regiao = (janela_app.left, janela_app.top, janela_app.width, janela_app.height)
    screenshot = pyautogui.screenshot(region=regiao)
    screenshot.save(nome_print)

    imagem = cv2.imread(nome_print)
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)

    # image_to_data (em vez de image_to_string) dá a caixa delimitadora (top/bottom) de
    # cada linha, necessária para recortar e reler a linha do cartão isoladamente.
    dados_ocr = pytesseract.image_to_data(cinza, lang='eng', output_type=pytesseract.Output.DICT)
    linhas_dict = {}
    for i in range(len(dados_ocr['text'])):
        palavra = dados_ocr['text'][i].strip()
        if not palavra:
            continue
        chave = (dados_ocr['block_num'][i], dados_ocr['par_num'][i], dados_ocr['line_num'][i])
        topo = dados_ocr['top'][i]
        base = topo + dados_ocr['height'][i]
        if chave not in linhas_dict:
            linhas_dict[chave] = {"palavras": [], "top": topo, "bottom": base}
        linhas_dict[chave]["palavras"].append(palavra)
        linhas_dict[chave]["top"] = min(linhas_dict[chave]["top"], topo)
        linhas_dict[chave]["bottom"] = max(linhas_dict[chave]["bottom"], base)

    linhas_ocr = sorted(linhas_dict.values(), key=lambda l: l["top"])
    for l in linhas_ocr:
        l["texto"] = " ".join(l["palavras"])

    tela_reconhecida = pagina_parece_fatura(linhas_ocr)

    # Procura a marcação de cabeçalho do cartão nos blocos do app
    for idx, linha in enumerate(linhas_ocr):
        if "Ourocard" in linha["texto"] or "Platinum" in linha["texto"] or "Estilo" in linha["texto"] or "Visa" in linha["texto"]:
            try:
                # O número do cartão está SEMPRE na linha imediatamente abaixo do cabeçalho do bloco.
                num_cartao = "Não Identificado"
                if idx + 1 < len(linhas_ocr):
                    linha_cartao = linhas_ocr[idx + 1]
                    num_cartao_lido = ler_numero_cartao(cinza, linha_cartao["top"], linha_cartao["bottom"])
                    if not num_cartao_lido or num_cartao_lido not in CARTOES_VALIDOS:
                        # O recorte dedicado não leu nada ou leu um número que não existe entre
                        # os cartões cadastrados (0808/1555/4025/8442, ex.: "1844", "2026" do
                        # ano) — tenta a leitura ampla da linha como segunda chance antes de
                        # descartar. Aceitar qualquer 4 dígitos sem checar a lista é o que
                        # deixava passar cartões que não existem.
                        cartao_fallback = re.search(r'\b\d{4}\b', linha_cartao["texto"])
                        if cartao_fallback and cartao_fallback.group(0) in CARTOES_VALIDOS:
                            num_cartao_lido = cartao_fallback.group(0)
                        else:
                            num_cartao_lido = None
                    if num_cartao_lido:
                        num_cartao = num_cartao_lido

                if num_cartao != "Não Identificado":
                    bloco_contexto = " ".join(l["texto"] for l in linhas_ocr[max(0, idx-3):min(len(linhas_ocr), idx+4)])
                    # Remove 'R$' colados do bloco lido para facilitar o batimento do padrão numérico puro
                    bloco_contexto_limpo = bloco_contexto.replace("R$", "").replace("RS", "")
                    bloco_contexto_upper = bloco_contexto_limpo.upper()

                    # O carimbo de data/hora do app aparece na linha logo abaixo do número do
                    # cartão (ex.: "16/07/2026 às 21:30:19") — é o desempate mais forte quando
                    # a mesma compra (nome E valor idênticos) se repete várias vezes no mês,
                    # caso em que o nome do estabelecimento sozinho não distingue nada.
                    data_tela = None
                    if idx + 2 < len(linhas_ocr):
                        m_data = re.search(r'(\d{2})/(\d{2})/(\d{4})', linhas_ocr[idx + 2]["texto"])
                        if m_data:
                            data_tela = f"{m_data.group(1)}.{m_data.group(2)}.{m_data.group(3)}"

                    # Quando o mesmo valor aparece em mais de uma compra do checklist (ex.: mesmo
                    # estabelecimento em datas diferentes), casar só pelo valor pode atribuir a
                    # compra errada. Desempata primeiro pela data exibida na tela e, na falta
                    # dela, pelo nome do estabelecimento também presente no bloco lido.
                    candidatos = [c for c in lista_controle
                                  if c["Cartao"] == "Não Identificado" and c["Valor_Str"] in bloco_contexto_limpo]
                    compra_escolhida = None
                    if data_tela:
                        for candidato in candidatos:
                            if candidato["Data"] == data_tela:
                                compra_escolhida = candidato
                                break
                    if compra_escolhida is None:
                        for candidato in candidatos:
                            primeira_palavra = candidato["Estabelecimento"].split()[0] if candidato["Estabelecimento"] else ""
                            if len(primeira_palavra) >= 4 and primeira_palavra in bloco_contexto_upper:
                                compra_escolhida = candidato
                                break
                    if compra_escolhida is None and candidatos:
                        compra_escolhida = candidatos[0]

                    if compra_escolhida is not None:
                        compra_escolhida["Cartao"] = num_cartao

                        if num_cartao == "0808":
                            compra_escolhida["Responsavel"] = "George (Principal)"
                        elif num_cartao == "1555":
                            compra_escolhida["Responsavel"] = "Virtual / Internet"
                        else:
                            compra_escolhida["Responsavel"] = "Markleny"

                        print(f" -> [OK] {compra_escolhida['Data']} - {compra_escolhida['Estabelecimento']} | R$ {compra_escolhida['Valor_Str']} -> Cartão {num_cartao} ({compra_escolhida['Responsavel']})")
            except Exception:
                continue

    return cinza, tela_reconhecida

def rolar_e_processar_pagina(janela_app, pasta_prints, pag_num, lista_controle, centro_x, direcao):
    """ Arrasta e só tira o print/roda o OCR DEPOIS do movimento mas ANTES de soltar o botão.
    Nesse instante a tela está exatamente onde o arrasto a deixou — soltar o botão é o que
    dispara o "fling" de inércia do app, que pode continuar deslizando por vários segundos e
    rolar bem mais do que o pretendido (confirmado por vídeo: 3-4s seguidos de tela ainda
    mudando bastante depois de soltar). Capturando antes de soltar, esse fling nunca chega a
    acontecer durante a leitura — não precisamos mais adivinhar quanto tempo esperar depois. """
    distancia = int(janela_app.height * 0.6)
    y_partida = janela_app.top + int(janela_app.height * (0.8 if direcao == "frente" else 0.2))

    pyautogui.moveTo(centro_x, y_partida)
    time.sleep(0.2)
    pyautogui.mouseDown(button='left')
    # Pausa segurando parado antes de iniciar o arrasto: o app precisa registrar
    # o "toque" antes do movimento, senão o clique+movimento imediato não é
    # reconhecido como gesto de rolagem.
    time.sleep(0.5)
    # Movimento em deltas relativos (mouse_event), não teleporte absoluto (SetCursorPos):
    # o Phone Link só reconhece como arrasto os incrementos que um mouse físico gera.
    arrastar_mouse_relativo(-distancia if direcao == "frente" else distancia)
    # Pequena pausa só para o app espelhado renderizar a posição final do arrasto.
    time.sleep(0.5)

    resultado = processar_pagina_atual(janela_app, pasta_prints, pag_num, lista_controle)

    pyautogui.mouseUp(button='left')
    # Não precisamos mais esperar a inércia pós-solta assentar: o que importava já foi
    # capturado com o botão ainda pressionado. Uma pausa curta só evita brigar com o
    # próximo toque enquanto o app ainda está processando a soltura.
    time.sleep(0.8)

    return resultado

def garantir_foco_janela(janela_app):
    """ pyautogui.press() manda a tecla para qualquer janela que estiver em foco no Windows
    naquele instante — diferente do arrasto de mouse, que clica nas coordenadas da janela e
    a foca de novo a cada vez. Se o usuário clicar em qualquer outra janela (ex.: no VS Code,
    lendo o chat) enquanto o script roda, as setas param de chegar no Ourocard: a tela fica
    parada de verdade e o script conclui "fim da lista" bem cedo, sem ter rolado nada.
    Reforçar o foco antes de cada tentativa de tecla evita isso. """
    try:
        if janela_app.isMinimized:
            janela_app.restore()
        janela_app.activate()
        time.sleep(0.1)
    except Exception:
        pass

def rolar_teclado_e_processar(janela_app, pasta_prints, pag_num, lista_controle, direcao, repeticoes=3, atraso=0.15):
    """ Rola com a seta do teclado (down/up) em vez de arrasto de mouse — confirmado pelo
    usuário que não sofre da rolagem excessiva por inércia (fling) que o arrasto tinha.
    Só teclas de seta, nunca um clique: um clique em algum item da lista abriria o detalhe
    do lançamento em vez de rolar a lista. """
    garantir_foco_janela(janela_app)
    tecla = "down" if direcao == "frente" else "up"
    for _ in range(repeticoes):
        pyautogui.press(tecla)
        time.sleep(atraso)
    # Pequena pausa para o app espelhado renderizar a posição final antes do print.
    time.sleep(0.3)
    return processar_pagina_atual(janela_app, pasta_prints, pag_num, lista_controle)

def varrer_lista(janela_app, pasta_prints, lista_controle, total_compras, centro_x, direcao, pag_inicial, max_paginas, paciencia=5):
    """ Percorre a lista rolando na direção indicada, reconciliando o que conseguir a cada
    página. Desiste depois de `paciencia` páginas seguidas sem nenhum item novo — mesmo
    critério simples do ajuste fino via teclado (`ajuste_fino_teclado()`), que na prática se
    mostrou mais confiável que comparar pixels entre capturas. A comparação de pixels
    ("tela parada") funcionava para o arrasto de mouse (passos grandes, ~60% da altura da
    janela), mas com o teclado cada toque de seta move pouco: a diferença de pixels entre
    duas capturas fica pequena mesmo com progresso genuíno, então esse critério declarava
    "fim da lista" cedo demais — só 2 páginas sem match já bastava, mesmo rolando de verdade. """
    paginas_sem_progresso = 0
    paginas_nao_reconhecidas = 0
    pag_num = pag_inicial

    for indice_pagina in range(max_paginas):
        pag_num += 1
        compras_antes = contar_reconciliadas(lista_controle)

        if indice_pagina == 0:
            # Primeira página da rodada: a tela já está na posição certa (herdada da
            # rodada/página anterior), não precisa rolar antes de capturar.
            _, tela_reconhecida = processar_pagina_atual(janela_app, pasta_prints, pag_num, lista_controle)
        elif MODO_ROLAGEM == "teclado":
            _, tela_reconhecida = rolar_teclado_e_processar(
                janela_app, pasta_prints, pag_num, lista_controle, direcao)
        else:
            _, tela_reconhecida = rolar_e_processar_pagina(
                janela_app, pasta_prints, pag_num, lista_controle, centro_x, direcao)
        compras_depois = contar_reconciliadas(lista_controle)

        if compras_depois == compras_antes:
            # A leitura pode falhar por um motivo pontual (blur do scroll ainda
            # assentando, etc.). Antes de contar a página como "sem progresso", recaptura
            # a MESMA tela (sem rolar) e tenta de novo — uma chance extra barata.
            time.sleep(1.0)
            pag_num += 1
            _, tela_reconhecida = processar_pagina_atual(janela_app, pasta_prints, pag_num, lista_controle)
            compras_depois = contar_reconciliadas(lista_controle)

        # Se a tela parar de parecer a lista de lançamentos (sem "Ourocard"/"Platinum"/
        # "Fatura" em lugar nenhum) por 2 páginas seguidas, a sessão provavelmente expirou
        # — continuar rolando e lendo OCR de uma tela de erro não leva a lugar nenhum.
        if tela_reconhecida:
            paginas_nao_reconhecidas = 0
        else:
            paginas_nao_reconhecidas += 1
            if paginas_nao_reconhecidas >= 2:
                raise SessaoExpiradaError(
                    f"A tela não parece mais a fatura do Ourocard na página {pag_num} "
                    f"(2 capturas seguidas sem nenhum texto de referência do app)."
                )

        if compras_depois >= total_compras:
            break

        if compras_depois == compras_antes:
            paginas_sem_progresso += 1
        else:
            paginas_sem_progresso = 0

        if paginas_sem_progresso >= paciencia:
            print(f"\n[Aviso] Fim da lista alcançado rolando '{direcao}' (nenhum item novo em "
                  f"{paginas_sem_progresso} páginas seguidas).")
            break

    return pag_num

def ajuste_fino_teclado(janela_app, pasta_prints, lista_controle, total_compras, pag_atual):
    """ Passo 2c: ajuste fino via teclado (seta para cima/para baixo) para os últimos itens
    pendentes depois das rodadas de arrasto de mouse. Um arrasto de página inteira pode
    pular direto por cima do único item que falta; nudges pequenos de teclado permitem
    caçar exatamente onde ele está. Só teclas de seta — nunca um clique, que abriria o
    detalhe do lançamento em vez de rolar a lista. """
    pag_num = pag_atual
    for tecla in ("down", "up"):
        if contar_reconciliadas(lista_controle) >= total_compras:
            break
        print(f"\n[Passo 2c] Ajuste fino via teclado ('{tecla}') para os itens pendentes...")
        tentativas_sem_progresso = 0
        paginas_nao_reconhecidas = 0

        for _ in range(15):
            compras_antes = contar_reconciliadas(lista_controle)

            garantir_foco_janela(janela_app)
            for _ in range(3):
                pyautogui.press(tecla)
                time.sleep(0.15)
            time.sleep(0.3)

            pag_num += 1
            _, tela_reconhecida = processar_pagina_atual(janela_app, pasta_prints, pag_num, lista_controle)
            compras_depois = contar_reconciliadas(lista_controle)

            if tela_reconhecida:
                paginas_nao_reconhecidas = 0
            else:
                paginas_nao_reconhecidas += 1
                if paginas_nao_reconhecidas >= 2:
                    raise SessaoExpiradaError(
                        f"A tela não parece mais a fatura do Ourocard na página {pag_num} "
                        f"(ajuste fino via teclado)."
                    )

            if compras_depois >= total_compras:
                break
            if compras_depois == compras_antes:
                tentativas_sem_progresso += 1
            else:
                tentativas_sem_progresso = 0
            if tentativas_sem_progresso >= 5:
                break

    return pag_num

def carregar_lista_controle_txt(caminho_txt):
    """ Passo 1: Lê o txt para saber exatamente a quantidade e quais são as compras """
    if not os.path.exists(caminho_txt):
        print(f"[Erro] Arquivo {caminho_txt} não encontrado.")
        return []
        
    with open(caminho_txt, 'r', encoding='utf-8', errors='ignore') as f:
        linhas = f.readlines()

    compras_esperadas = []
    
    for linha in linhas:
        linha_limpa = linha.strip()
        if not re.match(r'^\d{2}\.\d{2}\.\d{4}', linha_limpa):
            continue 
            
        if "PGTO DEBITO" in linha_limpa or "SALDO FATURA" in list(linha_limpa.upper().split()):
            continue
            
        valores = re.findall(r'-?\b\d+,\d{2}\b', linha_limpa)
        if valores:
            valor_str = valores[0]
            if valor_str.startswith("-"):
                continue 
                
            data_compra = linha_limpa[:10]
            resto = linha_limpa[10:].strip()
            
            # Limpeza do Estabelecimento para o Excel
            resto_limpo = resto.replace("BR", "").replace(valor_str, "").strip()
            resto_limpo = re.sub(r'\s+0,00\s*$', '', resto_limpo)
            resto_limpo = re.sub(r'\s+0,00\s+0,00\s*$', '', resto_limpo)
            estabelecimento = re.sub(r'\s+', ' ', resto_limpo).strip()
            
            compras_esperadas.append({
                "Data": data_compra,
                "Estabelecimento": establishment.upper() if 'establishment' in locals() else estabelecimento.upper(),
                "Valor_Str": valor_str,
                "Valor_Float": float(valor_str.replace('.', '').replace(',', '.')),
                "Cartao": "Não Identificado",
                "Responsavel": "Pendente Visão OCR"
            })
            
    return compras_esperadas

def executar_fluxo_reconciliacao():
    arquivo_txt = "OUROCARD_PLATINUM_ESTILO_VISA-Ago_26.txt"
    
    lista_controle = carregar_lista_controle_txt(arquivo_txt)
    total_compras = len(lista_controle)
    
    if total_compras == 0:
        print("[Erro] Nenhuma compra válida foi mapeada no TXT.")
        return
        
    print(f"[Passo 1 Concluído] Checklist mapeado pelo TXT.")
    print(f" -> Total de lançamentos para validar: {total_compras}")
    print("-" * 70)

    print("Buscando a janela do Ourocard para validação...")
    janelas = gw.getWindowsWithTitle('Ourocard')
    if not janelas:
        print("[Erro] Janela do 'Ourocard' não encontrada.")
        return
    
    janela_app = janelas[0]
    if janela_app.isMinimized:
        janela_app.restore()
    janela_app.activate()
    time.sleep(2.0)

    pasta_prints = "prints_fatura_validacao"
    if not os.path.exists(pasta_prints):
        os.makedirs(pasta_prints)

    print(f"[Passo 2 Iniciado] Capturando telas com rolagem estendida (modo: {MODO_ROLAGEM})...")

    # O teclado avança bem menos por passo do que o arrasto de mouse (para o qual esse teto
    # foi calibrado originalmente), então precisa de mais páginas para cobrir a lista inteira.
    max_paginas = 60
    centro_x = janela_app.left + (janela_app.width // 2)

    # A aceleração de ponteiro do Windows distorce a sequência de micro-movimentos relativos
    # do arrasto (movimentos rápidos em sequência podem ser amplificados de forma não-linear
    # pelo SO), fazendo a rolagem no app passar do ponto pretendido de forma inconsistente.
    # Desativa durante o Passo 2/2b e restaura a configuração original do usuário ao final.
    config_mouse_original = obter_configuracao_mouse()
    definir_configuracao_mouse([config_mouse_original[0], config_mouse_original[1], 0])
    try:
        # Alterna ida/volta até completar o checklist ou até uma rodada inteira não trazer
        # nenhum item novo (sinal de que continuar batendo de um lado para o outro não vai
        # ajudar mais — o que sobrou é um problema de leitura, não de cobertura de tela).
        direcao = "frente"
        pag_atual = 0
        max_rodadas = 6  # até 3 idas e voltas completas
        compras_reconciliadas = contar_reconciliadas(lista_controle)

        try:
            for rodada in range(1, max_rodadas + 1):
                compras_antes_rodada = compras_reconciliadas
                rotulo_passo = "Passo 2" if rodada == 1 else f"Passo 2b (rodada {rodada})"
                print(f"\n[{rotulo_passo}] Rolando '{direcao}'...")

                pag_atual = varrer_lista(janela_app, pasta_prints, lista_controle, total_compras,
                                          centro_x, direcao, pag_atual, max_paginas)
                compras_reconciliadas = contar_reconciliadas(lista_controle)

                if compras_reconciliadas >= total_compras:
                    break
                if compras_reconciliadas == compras_antes_rodada:
                    print(f"\n[Aviso] Rodada '{direcao}' completa sem nenhum item novo — mais "
                          f"idas e vindas não devem ajudar. Encerrando o Passo 2.")
                    break

                direcao = "voltar" if direcao == "frente" else "frente"

            if compras_reconciliadas < total_compras:
                pag_atual = ajuste_fino_teclado(janela_app, pasta_prints, lista_controle,
                                                 total_compras, pag_atual)
                compras_reconciliadas = contar_reconciliadas(lista_controle)
        except SessaoExpiradaError as erro:
            # Não deixa o trabalho já feito se perder: exporta com o que foi reconciliado
            # até aqui e avisa claramente o motivo, em vez de continuar rolando no vazio.
            print(f"\n[Erro] Sessão do Ourocard provavelmente expirou: {erro}")
            print("[Erro] Abra a fatura novamente no Phone Link e rode o script de novo para "
                  "tentar capturar os itens que ainda ficarem pendentes.")
    finally:
        definir_configuracao_mouse(config_mouse_original)

    if compras_reconciliadas >= total_compras:
        print("\n[Sucesso] Todos os lançamentos do TXT foram reconciliados com precisão!")
    else:
        print(f"\n[Aviso] Encerrado com {compras_reconciliadas} de {total_compras} reconciliados "
              f"mesmo após a volta — os restantes ficam como 'Pendente Visão OCR'.")

    print("\n" + "="*70)
    print("[Passo 3] Ordenando dados cronológicos e exportando para Excel...")
    df_final = pd.DataFrame(lista_controle)
    
    # Ordenação estrita por data (Mais antiga primeiro)
    df_final['Data_Obj'] = pd.to_datetime(df_final['Data'], format='%d.%m.%Y')
    df_final = df_final.sort_values(by='Data_Obj', ascending=True)
    
    df_salvar = df_final[["Data", "Estabelecimento", "Cartao", "Responsavel", "Valor_Float"]].rename(
        columns={"Valor_Float": "Valor (R$)", "Cartao": "Cartão", "Responsavel": "Responsável"}
    )
    
    nome_excel = "Fatura_Julho_Reconciliada_Perfeita.xlsx"
    df_salvar.to_excel(nome_excel, index=False)
    
    print(f"Planilha Excel consolidada gerada: {nome_excel}")
    print(f"Resumo da Execução: {compras_reconciliadas} de {total_compras} transações validadas.")
    print("="*70)
    print(df_salvar.to_string())

if __name__ == "__main__":
    executar_fluxo_reconciliacao()