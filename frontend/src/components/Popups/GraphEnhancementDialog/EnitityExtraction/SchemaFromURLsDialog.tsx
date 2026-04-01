import { Button, Dialog, TextArea, TextInput, Typography } from '@neo4j-ndl/react';
import { useCallback, useState } from 'react';
import { bootstrapSchemaAPI } from '../../../../services/SchemaFromURLsAPI';
import { useFileContext } from '../../../../context/UsersFiles';
import ButtonWithToolTip from '../../../UI/ButtonWithToolTip';
import { showNormalToast } from '../../../../utils/Toasts';
import PatternContainer from './PatternContainer';
import SchemaViz from '../../../Graph/SchemaViz';
import { OptionType, TupleType } from '../../../../types';
import { extractOptions, updateSourceTargetTypeOptions } from '../../../../utils/Utils';

interface SchemaFromURLsProps {
  open: boolean;
  onClose: () => void;
  onApply: (
    patterns: string[],
    nodes: OptionType[],
    rels: OptionType[],
    updatedSource: OptionType[],
    updatedTarget: OptionType[],
    updatedType: OptionType[]
  ) => void;
}

const SchemaFromURLsDialog = ({ open, onClose, onApply }: SchemaFromURLsProps) => {
  const [urlText, setUrlText] = useState<string>('');
  const [sampleSize, setSampleSize] = useState<number>(5);
  const [loading, setLoading] = useState<boolean>(false);
  const { model } = useFileContext();
  const {
    bootstrapNodes,
    setBootstrapNodes,
    bootstrapRels,
    setBootstrapRels,
    bootstrapPattern,
    setBootstrapPattern,
    sourceOptions,
    setSourceOptions,
    targetOptions,
    setTargetOptions,
    typeOptions,
    setTypeOptions,
  } = useFileContext();
  const [openGraphView, setOpenGraphView] = useState<boolean>(false);
  const [viewPoint, setViewPoint] = useState<string>('');

  const urls = urlText
    .split(/[\n,]/)
    .map((u) => u.trim())
    .filter(Boolean);

  const clickHandler = useCallback(async () => {
    if (!urls.length) {
      return;
    }
    setLoading(true);
    try {
      const response = await bootstrapSchemaAPI(model, urls, sampleSize);
      const { status, message, data } = response.data;
      if (status === 'Success' && data) {
        const { nodes, relationships } = data;
        const schemaTuples: TupleType[] = relationships.map((rel) => ({
          value: `${rel.source},${rel.type},${rel.target}`,
          label: `${rel.source} -[:${rel.type}]-> ${rel.target}`,
          source: rel.source,
          target: rel.target,
          type: rel.type,
        }));

        // If there are isolated nodes (no relationship), we need to preserve them
        const nodeOptions: OptionType[] = nodes.map((n) => ({ value: n, label: n }));
        const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(schemaTuples);

        // Merge isolated nodes (those not appearing in any relationship)
        const relNodeValues = new Set(nodeLabelOptions.map((n) => n.value));
        for (const n of nodeOptions) {
          if (!relNodeValues.has(n.value)) {
            nodeLabelOptions.push(n);
          }
        }

        setBootstrapNodes(nodeLabelOptions);
        setBootstrapRels(relationshipTypeOptions);
        setBootstrapPattern(schemaTuples.map((t) => t.label));
      } else {
        showNormalToast(message ?? 'No schema could be extracted from the provided URLs.');
      }
    } catch (error: any) {
      showNormalToast(error?.message ?? 'Unexpected error occurred.');
    } finally {
      setLoading(false);
    }
  }, [model, urls, sampleSize]);

  const handleRemovePattern = (pattern: string) => {
    const updatedPatterns = bootstrapPattern.filter((p) => p !== pattern);
    if (!updatedPatterns.length) {
      setBootstrapPattern([]);
      setBootstrapNodes([]);
      setBootstrapRels([]);
      return;
    }
    const updatedTuples: TupleType[] = updatedPatterns
      .map((item) => {
        const match = item.match(/^(.+?) -\[:(.+?)\]-> (.+)$/);
        if (match) {
          const [source, type, target] = match.slice(1).map((s) => s.trim());
          return { value: `${source},${type},${target}`, label: item, source, target, type };
        }
        return null;
      })
      .filter(Boolean) as TupleType[];
    const { nodeLabelOptions, relationshipTypeOptions } = extractOptions(updatedTuples);
    setBootstrapPattern(updatedPatterns);
    setBootstrapNodes(nodeLabelOptions);
    setBootstrapRels(relationshipTypeOptions);
  };

  const handleSchemaView = () => {
    setOpenGraphView(true);
    setViewPoint('showSchemaView');
  };

  const handleApply = async () => {
    if (!onApply) {
      return;
    }
    const [newSourceOptions, newTargetOptions, newTypeOptions] = await updateSourceTargetTypeOptions({
      patterns: bootstrapPattern.map((label) => ({ label, value: label })),
      currentSourceOptions: sourceOptions,
      currentTargetOptions: targetOptions,
      currentTypeOptions: typeOptions,
      setSourceOptions,
      setTargetOptions,
      setTypeOptions,
    });
    onApply(bootstrapPattern, bootstrapNodes, bootstrapRels, newSourceOptions, newTargetOptions, newTypeOptions);
    onClose();
  };

  const handleCancel = () => {
    setBootstrapNodes([]);
    setBootstrapRels([]);
    setBootstrapPattern([]);
    setUrlText('');
    onClose();
  };

  return (
    <>
      <Dialog
        size='medium'
        isOpen={open}
        onClose={handleCancel}
        htmlAttributes={{ 'aria-labelledby': 'bootstrap-schema-dialog-title' }}
      >
        <Dialog.Header>Bootstrap Schema from Web URLs</Dialog.Header>
        <Dialog.Content className='n-flex n-flex-col n-gap-token-4'>
          <Typography variant='body-small'>
            Enter URLs to sample (one per line or comma-separated). The LLM will extract a suggested schema from a
            sample of pages.
          </Typography>
          <TextArea
            label='URLs to sample'
            helpText='e.g. https://example.com/products/, https://example.com/features/'
            isFluid={true}
            style={{ resize: 'vertical' }}
            value={urlText}
            htmlAttributes={{
              onChange: (e) => {
                setUrlText(e.target.value);
                setBootstrapPattern([]);
                setBootstrapNodes([]);
                setBootstrapRels([]);
              },
              rows: 4,
            }}
          />
          <div className='flex items-center gap-4 mt-2'>
            <Typography variant='body-medium'>Sample size:</Typography>
            <TextInput
              htmlAttributes={{
                id: 'bootstrap-sample-size',
                type: 'number',
                min: 1,
                max: 20,
                'aria-label': 'Number of pages to sample',
              }}
              value={String(sampleSize)}
              isFluid={false}
              onChange={(e) => {
                const val = parseInt(e.target.value, 10);
                if (!isNaN(val) && val > 0 && val <= 20) {
                  setSampleSize(val);
                }
              }}
            />
            <div className='ml-auto'>
              <ButtonWithToolTip
                placement='top'
                label='Discover schema button'
                text={!urls.length ? 'Enter at least one URL' : `Discover schema from up to ${sampleSize} pages`}
                loading={loading}
                disabled={!urls.length || loading}
                onClick={clickHandler}
              >
                Discover Schema
              </ButtonWithToolTip>
            </div>
          </div>
          {bootstrapPattern.length > 0 && (
            <div className='mt-4'>
              <PatternContainer
                pattern={bootstrapPattern}
                handleRemove={handleRemovePattern}
                handleSchemaView={handleSchemaView}
                highlightPattern=''
                nodes={bootstrapNodes}
                rels={bootstrapRels}
              />
            </div>
          )}
          <Dialog.Actions className='mt-3'>
            <Button fill='outlined' onClick={handleCancel}>
              Cancel
            </Button>
            <Button onClick={handleApply} isDisabled={!bootstrapPattern.length || loading}>
              Apply Schema
            </Button>
          </Dialog.Actions>
        </Dialog.Content>
      </Dialog>
      {openGraphView && (
        <SchemaViz
          open={openGraphView}
          setGraphViewOpen={setOpenGraphView}
          viewPoint={viewPoint}
          nodeValues={bootstrapNodes}
          relationshipValues={bootstrapRels}
        />
      )}
    </>
  );
};

export default SchemaFromURLsDialog;
