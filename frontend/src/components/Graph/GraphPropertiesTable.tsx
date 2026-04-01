import { useState } from 'react';
import { GraphLabel, Typography } from '@neo4j-ndl/react';
import { GraphPropertiesTableProps } from '../../types';

const TRUNCATE_LENGTH = 200;

const GraphPropertiesTable = ({ propertiesWithTypes }: GraphPropertiesTableProps): JSX.Element => {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggleExpanded = (key: string) => {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className='flex w-full flex-col break-all px-4 text-sm' data-testid='viz-details-pane-properties-table'>
      <div className='mb-1 flex! flex-row pl-2'>
        <Typography variant='body-medium' className='basis-1/3'>
          Key
        </Typography>
        <Typography variant='body-medium'>Value</Typography>
      </div>
      {propertiesWithTypes
        .filter(({ value }) => value !== undefined && value !== null && value !== '' && !Array.isArray(value))
        .map(({ key, value }) => {
          const strValue = String(value);
          const isLong = strValue.length > TRUNCATE_LENGTH;
          const isExpanded = expanded[key] ?? false;
          const displayValue = isLong && !isExpanded ? `${strValue.slice(0, TRUNCATE_LENGTH)  }…` : strValue;

          return (
            <div key={key} className='border-palette-neutral-border-weak flex! border-t py-1 pl-2 first:border-none'>
              <div className='shrink basis-1/3 overflow-hidden whitespace-nowrap'>
                <GraphLabel
                  type='propertyKey'
                  className='pointer-events-none max-w-full! text-ellipsis'
                  htmlAttributes={{
                    tabIndex: -1,
                  }}
                >
                  {key}
                </GraphLabel>
              </div>
              <div className='ml-2 flex-1'>
                <span className='whitespace-pre-wrap'>{displayValue}</span>
                {isLong && (
                  <button
                    onClick={() => toggleExpanded(key)}
                    className='mt-1 block text-xs text-primary-50 hover:underline focus:outline-none'
                  >
                    {isExpanded ? 'Show less' : 'Show more'}
                  </button>
                )}
              </div>
            </div>
          );
        })}
    </div>
  );
};

export default GraphPropertiesTable;
