import api from '../API/Index';

export interface BootstrapSchemaResponse {
  nodes: string[];
  relationships: { source: string; type: string; target: string }[];
  raw_nodes: string[];
  raw_relationships: string[];
}

const bootstrapSchemaAPI = async (
  model: string,
  urls: string[],
  sampleSize: number = 5
): Promise<{ data: { status: string; data?: BootstrapSchemaResponse; message?: string } }> => {
  const formData = new FormData();
  formData.append('model', model);
  formData.append('urls', urls.join(','));
  formData.append('sample_size', String(sampleSize));

  return api.post('/schema/bootstrap', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export { bootstrapSchemaAPI };
