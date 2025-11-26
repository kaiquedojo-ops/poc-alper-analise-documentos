###
# Configuração inicial para POC
###

def lambda_handler(event, none):
    import os

    os.environ["HOME"] = "/tmp"   # força o cache e arquivos para /tmp
    os.environ["XDG_CACHE_HOME"] = "/tmp"  # necessário para algumas libs
    os.environ['PADDLEOCR_HOME'] = '/tmp/.paddleocr'

    from paddleocr import PaddleOCR
    import logging
    import pandas as pd
    import io
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()]
    )
    import boto3
    import json
    from openai import OpenAI

    from urllib.parse import unquote_plus
    from botocore.exceptions import ClientError

    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId="API_OPEN_AI")
    secret_string = response["SecretString"]
    secret_dict = json.loads(secret_string)

    client = OpenAI(
        api_key=secret_dict['value']
    )
    s3 = boto3.client('s3')
    BUCKET_NAME = "alper-analise-documentos"

    logging.info("--> Carregando Paddle OCR")
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_angle_cls=True,
        #text_det_unclip_ratio=2.3,
        lang="pt"
    )

    logging.info("--> Recebendo mensagem com a key da localização do documento")
    try:
        record = event['Records'][0]
        encoded_key = json.loads(record["body"])['Records'][0]['s3']['object']['key']
        key = unquote_plus(encoded_key)
    except Exception as e:
        print(f"Erro ao extrair key do evento: {e}")
        raise e
    print(f"Key {key}")
    logging.info("--> Baixando documento")
    try:
        pdf_obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
        pdf_bytes = pdf_obj['Body'].read()
        print("✅ PDF baixado com sucesso do S3")
    except Exception as e:
        print(f"❌ Erro ao baixar PDF: {e}")
        return {"error": str(e)}

    file_name = os.path.basename(key) 
    base_name = os.path.splitext(file_name)[0] # nome do arquivo
    print(f"nome do arquivo {base_name}")

    # caminho temporário para salvar o PDF
    input_path = "/tmp/documento.pdf"

    # salva o PDF baixado do S3
    with open(input_path, "wb") as f:
        f.write(pdf_bytes)
    
    logging.info("--> Executando OCR")
    
    results = ocr.predict(
        input=input_path)
    

    logging.info("--> Criando dicionário com o texto e sua posição")
    texts_polys = []

    # Percorre cada elemento de result
    for item in results:
        data = []
        # Para cada texto e polígono dentro do item
        for text, poly in zip(item['rec_texts'], item['rec_polys']):
            x_coords = [int(p[0]) for p in poly]
            y_coords = [int(p[1]) for p in poly]
            x_min, y_min = min(x_coords), min(y_coords)
            x_max, y_max = max(x_coords), max(y_coords)

            data.append({
                "text": text,
                "box": [x_min, y_min, x_max, y_max]
            })
        
        # Adiciona a lista de dados processados deste item à lista geral
        texts_polys.append(data)

    logging.info(f"--> texts_polys {texts_polys}")
    print("text_polys")
    print(texts_polys)

    logging.info("--> Para cada json da página, extraia valores")

    consolidado = {}  # Aqui ficará o JSON final

    import re
    import unicodedata

    regex_numero = re.compile(r'\b\d+\b')

    def normalizar(texto):
        return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('utf-8').lower()

    def filtrar_paginas(paginas, digitos_mais=3):
        paginas_filtradas = []
        indices_selecionados = []

        # Criar regex que pega singular ou plural das palavras-chave
        palavras_chave = ['premio', 'valor', 'total', 'cobertura', 'vigencia', 'impostos', 'iof' 'pis']
        # Adiciona um "s?" no final de cada para pegar singular/plural
        padrao = re.compile(r'\b(?:' + '|'.join([p + 's?' for p in palavras_chave]) + r')\b', re.IGNORECASE)

        for i, pagina in enumerate(paginas):
            numeros = []
            texto_completo = ""

            for item in pagina:
                texto_item = item['text']
                texto_completo += " " + texto_item
                matches = regex_numero.findall(texto_item)
                numeros.extend([int(m) for m in matches])
            
            texto_norm = normalizar(texto_completo)
            media_digitos = sum(len(str(n)) for n in numeros) / len(numeros) if numeros else 0

            cond_numero_grande = any(len(str(n)) >= media_digitos + digitos_mais for n in numeros)
            cond_palavra_chave = bool(padrao.search(texto_norm))  # verifica singular ou plural

            if cond_numero_grande or cond_palavra_chave:
                paginas_filtradas.append(pagina)
                indices_selecionados.append(i + 1)

        return paginas_filtradas, indices_selecionados

    paginas_filtradas, indices_selecionados = filtrar_paginas(texts_polys)

    print(f"indices indices_selecionados {indices_selecionados}")

    def merge_page_data(consolidado, novo):
        for chave, valor in novo.items():

            # Ignora valores nulos ou vazios
            if valor is None:
                continue
            if isinstance(valor, str) and valor.strip() == "":
                continue

            # Se chave ainda não existe → adiciona normalmente
            if chave not in consolidado:
                consolidado[chave] = valor
                continue

            # -------- Regras específicas --------

            # 1. Impostos → mesclar sem sobrescrever
            if chave == "impostos":
                for imposto, v in valor.items():
                    if imposto not in consolidado[chave]:
                        consolidado[chave][imposto] = v
                continue

            # 2. Coberturas → NÃO mesclar, manter apenas a primeira
            if chave == "coberturas":
                continue  # ignora coberturas posteriores

            # 3. Valores simples → manter o primeiro encontrado
            continue  # não sobrescreve
            


    # -----------------------
    # GERANDO HASH POR ENQUANTO DOS NÚMEROS
    # -----------------------

    import re
    import hashlib
    import json

    # -------------------------- FUNÇÕES DE SUPORTE --------------------------

    def generate_sha256_hash(text):
        """Gera um hash SHA256 para o texto fornecido (incluindo espaços)."""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    # Regex para encontrar qualquer sequência que pareça um número, incluindo espaços internos.
    number_pattern = re.compile(
        r'\b(?:\d+[.,\/\-()\s]*)+\d+\b'
    )

    # -------------------------- FUNÇÃO PRINCIPAL MODIFICADA --------------------------

    def hash_all_numbers_with_mapping(ocr_list_of_lists):
        """
        Processa a lista de listas de OCR, aplicando hash SHA256 em números
        e gerando um dicionário de mapeamento (Original -> Hash).
        """
        hashed_ocr_data = []
        # Dicionário para armazenar o mapeamento (valor original como chave, hash como valor)
        hash_mapping = {}

        for page in ocr_list_of_lists:
            hashed_page = []
            for item in page:
                original_text = item['text']
                new_item = item.copy() 
                
                # Use uma cópia do texto para a substituição de forma que o original_text
                # permaneça intacto para a lógica de mapeamento.
                current_text = original_text
                
                # Encontra todas as ocorrências de números no texto atual
                matches = list(number_pattern.finditer(current_text))
                
                if matches:
                    def replace_with_hash(match):
                        """
                        Função de substituição que gera o hash e
                        ARMAZENA o mapeamento no dicionário.
                        """
                        number_found = match.group(0)
                        
                        # 1. Gera o Hash
                        number_hash = generate_sha256_hash(number_found)
                        
                        # 2. Armazena o mapeamento (se ainda não estiver lá)
                        if number_found not in hash_mapping:
                            hash_mapping[number_found] = number_hash
                            
                        # 3. Retorna o hash para a substituição na string
                        return number_hash
                    
                    # Aplica a substituição usando a função que armazena o mapeamento
                    new_item['text'] = number_pattern.sub(replace_with_hash, current_text)

                hashed_page.append(new_item)
            
            hashed_ocr_data.append(hashed_page)
        
        # Retorna tanto os dados de OCR processados quanto o dicionário de mapeamento
        return hashed_ocr_data, hash_mapping


    # 🚀 Execução do Script

    texts_polys, mapping_dict = hash_all_numbers_with_mapping(paginas_filtradas)




    # -------------------------
    # LOOP PRINCIPAL DE EXTRAÇÃO
    # -------------------------

    for data in texts_polys:
        prompt = f"""
        Você receberá textos OCR de uma única página de um documento de seguro. Extraia SOMENTE as informações que estiverem claramente presentes nesta página.

        Atenção:
        - "nome_segurado" e "endereco_segurado" são dados do SEGURADO (cliente).
        - "cnpj_seguradora" pertence à SEGURADORA (empresa que emite a apólice).
        - NÃO confunda segurado com seguradora.

        # ⚠️ REGRA DE HASHEAMENTO:
        # Se um valor (como um número de apólice, proposta ou valor) for encontrado em formato alfanumérico longo (ex: 64 caracteres), 
        # considere-o como um valor hasheado VÁLIDO e o inclua no JSON. Não tente deshashear ou limpar este valor.

        - "vigencia":
            • Só inclua essa chave se a página contiver CLARAMENTE as DUAS datas (data de início E data de fim).
            • Ambas as datas podem estar em formato **DD/MM/AAAA** ou no formato **HASH SHA256** (64 caracteres alfanuméricos).
            
            • Exemplos válidos de vigência COMPLETA:
                - "de 20/06/2024 a 20/06/2025" (Datas claras)
                - "HASH_INICIO a HASH_FIM" (Datas hasheadas)
                - "a partir das 24h do dia HASH_INICIO até as 24h do dia HASH_FIM"
                
            • O formato final deve ser: "**VALOR_DATA_INICIO a VALOR_DATA_FIM**". 
            (Onde VALOR_DATA pode ser o DD/MM/AAAA ou o HASH de 64 caracteres).
            
            • Exemplos que DEVEM ser ignorados:
                - "vigência até 20/06/2025"
                - "20/06/2025"
                - qualquer caso onde só haja uma data, mesmo que hasheada.

            • Se houver qualquer dúvida, NÃO incluir a chave "vigencia".

        - Se tiver dúvida sobre qualquer campo, NÃO inclua ele no JSON final.

        Estrutura de referência das chaves permitidas (NÃO é para copiar tudo — use apenas as chaves que tiverem valor nesta página):

        {{
        "nome_segurado": null,
        "cnpj_seguradora": null,
        "endereco_segurado": null,
        "vigencia": null,
        "valor_premio": null,
        "tipo_seguro": null,
        "coberturas": [],
        "numero_apolice": null,
        "numero_proposta": null,
        "impostos": {{}}
        }}

        ⚠️ Regras obrigatórias:
        - Inclua no JSON final **somente** as chaves cujo valor tenha sido encontrado na página.
        - Se o valor for nulo, vazio, formato inválido ou duvidoso → NÃO inclua a chave.
        - "coberturas" só deve existir se houver pelo menos 1 cobertura.
        - "impostos" só deve existir se houver pelo menos 1 imposto.
        - "impostos" deve ser um objeto no formato {{ "imposto_normalizado": "valor" }}.
        - Para normalizar nomes de impostos:
            * converter para minúsculas
            * remover pontos (.)
            * substituir espaços, hífens, barras e outros caracteres não alfanuméricos por "_"
            * comprimir múltiplos "_" consecutivos em um só
        - Retorne apenas JSON válido, sem comentários.

        Conteúdo OCR desta página:
        {json.dumps(data, indent=2)}
        """

        completion = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": "Você receberá textos extraídos via OCR. Retorne os valores em JSON chave:valor."},
                {"role": "user",   "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0
        )

        result_json = completion.choices[0].message.content
        print(f"Exibindo result_json {result_json}")

        try:
            parsed = json.loads(result_json)
        except json.JSONDecodeError:
            print("Retorno inválido — ignorado")
            continue

        # 🔥 Aqui você faz o merge incremental
        merge_page_data(consolidado, parsed)


    # -------------------------
    # CONSOLIDADO FINAL
    # -------------------------

    json_final = json.dumps(consolidado, ensure_ascii=False)

    print("\nJSON FINAL CONSOLIDADO:")
    print(json_final)

    # 1) Garantir que está como dict Python
    data_dict = json.loads(json_final)
    print(f"data_dict {data_dict}")
    

    bucket = "alper-analise-documentos"
    key = f"document_processed/resultados.xlsx"

    # -------------------------------------------------------------------------
    # 1. Normaliza o JSON em DataFrame
    # -------------------------------------------------------------------------
    df_novo = pd.json_normalize(data_dict)

    # -------------------------------------------------------------------------
    # 2. Coluna nome_arquivo como primeira
    # -------------------------------------------------------------------------
    df_novo["nome_arquivo"] = base_name
    cols = ["nome_arquivo"] + [c for c in df_novo.columns if c != "nome_arquivo"]
    df_novo = df_novo[cols]

    # -------------------------------------------------------------------------
    # 3. Tenta ler Excel existente do S3 para fazer append
    # -------------------------------------------------------------------------
    try:
        buffer_existente = io.BytesIO()
        s3.download_fileobj(bucket, key, buffer_existente)
        buffer_existente.seek(0)

        df_existente = pd.read_excel(buffer_existente)

        # concatena o novo embaixo
        df_final = pd.concat([df_existente, df_novo], ignore_index=True)

    except s3.exceptions.NoSuchKey:
        # arquivo ainda não existe
        df_final = df_novo

    except Exception as e:
        print("Erro ao abrir Excel existente, recriando arquivo:", e)
        df_final = df_novo

    # -------------------------------------------------------------------------
    # 4. Gerar Excel em memória (sem escrever em disco)
    # -------------------------------------------------------------------------
    buffer_out = io.BytesIO()
    df_final.to_excel(buffer_out, index=False)
    buffer_out.seek(0)

    # -------------------------------------------------------------------------
    # 5. Upload para S3
    # -------------------------------------------------------------------------
    s3.upload_fileobj(buffer_out, bucket, key)

    print(f"Excel atualizado em s3://{bucket}/{key}")


    # -------------------
    # SALVANDO O MAPPING DO HASH
    # --------------------

    def export_and_upload_to_s3(data_dict, file_name_base, bucket_name, destination_key):
        """
        Transfor ma um dicionário de mapeamento em Excel, salva no /tmp e faz o upload para o S3.
        
        data_dict: O dicionário de mapeamento (Original -> Hash).
        file_name_base: Nome base do arquivo local (ex: 'documento_mapping.xlsx').
        bucket_name: Nome do bucket S3.
        destination_key: Caminho completo no S3.
        """
        
        # 1. Define o caminho temporário obrigatório para ambientes R/O (ex: Lambda)
        temp_file_path = os.path.join("/tmp", file_name_base)
        
        # 2. Cria o DataFrame Pandas
        df = pd.DataFrame(data_dict.items(), columns=['Valor Original (Sem Hash)', 'Valor Com Hash (SHA256)'])

        # 3. Exporta o DataFrame para o arquivo Excel no diretório /tmp
        try:
            df.to_excel(temp_file_path, index=False)
            print(f"✅ Arquivo local temporário '{temp_file_path}' criado com sucesso.")
            
            # 4. Inicializa o cliente S3
            s3 = boto3.client('s3')
            
            # 5. Faz o Upload do arquivo local
            s3.upload_file(temp_file_path, bucket_name, destination_key)
            print(f"✅ Upload concluído para s3://{bucket_name}/{destination_key}")
            
        except Exception as e:
            print(f"❌ Erro durante o processamento ou upload: {e}")
            raise # Re-lança a exceção para que o handler Lambda saiba que falhou
        
        finally:
            # 6. Limpa o arquivo temporário (Boa prática em serverless)
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                print(f"🗑️ Arquivo temporário '{temp_file_path}' removido.")

    # --- Chamada da função (Exemplo de como ficaria a sua chamada no script principal) ---

    file_name_local = f"{base_name}_mapping.xlsx"
    destination_key_s3 = f"document_processed/{base_name}_mapping.xlsx"

    export_and_upload_to_s3(
        mapping_dict, 
        file_name_base=file_name_local, 
        bucket_name=bucket, 
        destination_key=destination_key_s3
    )