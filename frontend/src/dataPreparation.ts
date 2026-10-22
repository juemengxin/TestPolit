export interface PreparationField {
  name: string
  type: 'string' | 'integer' | 'number' | 'boolean' | 'null' | 'json'
  random_mode?: 'example' | 'rules'
  random?: boolean
  length?: number
  min?: number
  max?: number
  value: string
}

export interface PreparationDraft {
  count: number
  random_seed?: string
  fields: PreparationField[]
  output_format: 'json' | 'sql'
  dialect: 'sqlite' | 'mysql' | 'postgresql'
  table_name: string
  json_shape: 'object' | 'array'
}

export interface PreparationResult extends PreparationDraft {
  variables: Record<string, unknown>
  generated_json: string
  generated_sql: string
}

export function preparationDraft(config?: Partial<PreparationDraft>): PreparationDraft {
  return {
    count: config?.count || 1,
    random_seed: config?.random_seed,
    fields: config?.fields?.map(field => ({random:false,length:12,min:0,max:100,random_mode:field.random_mode||(field.random?'rules':'example'),...field})) || [{name: '', type: 'string', value: '',random:false,random_mode:'example',length:12,min:0,max:100}],
    output_format: config?.output_format || 'json',
    dialect: config?.dialect || 'sqlite',
    table_name: config?.table_name || '',
    json_shape: config?.json_shape || 'object',
  }
}
