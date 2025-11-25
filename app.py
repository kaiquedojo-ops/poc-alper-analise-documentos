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


    logging.info("--> Para cada json da página, extraia valores")

    consolidado = {}  # Aqui ficará o JSON final

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
            
    print("comentado")
    print(texts_polys)
    # # -------------------------
    # # LOOP PRINCIPAL DE EXTRAÇÃO
    # # -------------------------

    # for data in texts_polys:
    #     print(f"texts_polys: {texts_polys}")
    #     prompt = f"""
    #     Você receberá textos OCR de uma única página de um documento de seguro. Extraia SOMENTE as informações que estiverem claramente presentes nesta página.

    #     Atenção:
    #     - "nome_segurado" e "endereco_segurado" são dados do SEGURADO (cliente).
    #     - "cnpj_seguradora" pertence à SEGURADORA (empresa que emite a apólice).
    #     - NÃO confunda segurado com seguradora.

    #     - "vigencia":
    #         • Só inclua essa chave se a página contiver CLARAMENTE as DUAS datas:
    #         uma data de início E uma data de fim.
    #         • Exemplos válidos:
    #             - "de 20/06/2024 a 20/06/2025"
    #             - "a partir das 24h do dia 20/06/2024 até as 24h do dia 20/06/2025"
    #         • Exemplos que DEVEM ser ignorados:
    #             - "vigência até 20/06/2025"
    #             - "20/06/2025"
    #             - "vigência: 20/06/2025"
    #             - qualquer caso onde só haja uma data
    #         • O formato final deve ser: "DD/MM/AAAA a DD/MM/AAAA"
    #         • Se houver qualquer dúvida, NÃO incluir a chave "vigencia".

    #     - Se tiver dúvida sobre qualquer campo, NÃO inclua ele no JSON final.

    #     Estrutura de referência das chaves permitidas (NÃO é para copiar tudo — use apenas as chaves que tiverem valor nesta página):

    #     {{
    #     "nome_segurado": null,
    #     "cnpj_seguradora": null,
    #     "endereco_segurado": null,
    #     "vigencia": null,
    #     "valor_premio": null,
    #     "tipo_seguro": null,
    #     "coberturas": [],
    #     "numero_apolice": null,
    #     "numero_proposta": null,
    #     "impostos": {{}}
    #     }}

    #     ⚠️ Regras obrigatórias:
    #     - Inclua no JSON final **somente** as chaves cujo valor tenha sido encontrado na página.
    #     - Se o valor for nulo, vazio, formato inválido ou duvidoso → NÃO inclua a chave.
    #     - "coberturas" só deve existir se houver pelo menos 1 cobertura.
    #     - "impostos" só deve existir se houver pelo menos 1 imposto.
    #     - "impostos" deve ser um objeto no formato {{ "imposto_normalizado": "valor" }}.
    #     - Para normalizar nomes de impostos:
    #         * converter para minúsculas
    #         * remover pontos (.)
    #         * substituir espaços, hífens, barras e outros caracteres não alfanuméricos por "_"
    #         * comprimir múltiplos "_" consecutivos em um só
    #     - Retorne apenas JSON válido, sem comentários.

    #     Conteúdo OCR desta página:
    #     {json.dumps(data, indent=2)}
    #     """

    #     completion = client.chat.completions.create(
    #         model="gpt-4.1",
    #         messages=[
    #             {"role": "system", "content": "Você receberá textos extraídos via OCR. Retorne os valores em JSON chave:valor."},
    #             {"role": "user",   "content": prompt}
    #         ],
    #         response_format={"type": "json_object"},
    #         temperature=0
    #     )

    #     result_json = completion.choices[0].message.content
    #     print(f"Exibindo result_json {result_json}")

    #     try:
    #         parsed = json.loads(result_json)
    #     except json.JSONDecodeError:
    #         print("Retorno inválido — ignorado")
    #         continue

    #     # 🔥 Aqui você faz o merge incremental
    #     merge_page_data(consolidado, parsed)


    # # -------------------------
    # # CONSOLIDADO FINAL
    # # -------------------------

    # json_final = json.dumps(consolidado, ensure_ascii=False)

    # print("\nJSON FINAL CONSOLIDADO:")
    # print(json_final)

    # # 1) Garantir que está como dict Python
    # data_dict = json.loads(json_final)
    # print(f"data_dict {data_dict}")
    

    # bucket = "alper-analise-documentos"
    # key = f"document_processed/resultados.xlsx"

    # # -------------------------------------------------------------------------
    # # 1. Normaliza o JSON em DataFrame
    # # -------------------------------------------------------------------------
    # df_novo = pd.json_normalize(data_dict)

    # # -------------------------------------------------------------------------
    # # 2. Coluna nome_arquivo como primeira
    # # -------------------------------------------------------------------------
    # df_novo["nome_arquivo"] = base_name
    # cols = ["nome_arquivo"] + [c for c in df_novo.columns if c != "nome_arquivo"]
    # df_novo = df_novo[cols]

    # # -------------------------------------------------------------------------
    # # 3. Tenta ler Excel existente do S3 para fazer append
    # # -------------------------------------------------------------------------
    # try:
    #     buffer_existente = io.BytesIO()
    #     s3.download_fileobj(bucket, key, buffer_existente)
    #     buffer_existente.seek(0)

    #     df_existente = pd.read_excel(buffer_existente)

    #     # concatena o novo embaixo
    #     df_final = pd.concat([df_existente, df_novo], ignore_index=True)

    # except s3.exceptions.NoSuchKey:
    #     # arquivo ainda não existe
    #     df_final = df_novo

    # except Exception as e:
    #     print("Erro ao abrir Excel existente, recriando arquivo:", e)
    #     df_final = df_novo

    # # -------------------------------------------------------------------------
    # # 4. Gerar Excel em memória (sem escrever em disco)
    # # -------------------------------------------------------------------------
    # buffer_out = io.BytesIO()
    # df_final.to_excel(buffer_out, index=False)
    # buffer_out.seek(0)

    # # -------------------------------------------------------------------------
    # # 5. Upload para S3
    # # -------------------------------------------------------------------------
    # s3.upload_fileobj(buffer_out, bucket, key)

    # print(f"Excel atualizado em s3://{bucket}/{key}")