import { useState } from 'react';
import { Checkbox, TextInput, Flex, Typography } from '@neo4j-ndl/react';
import { webLinkValidation } from '../../../utils/Utils';
import useSourceInput from '../../../hooks/useSourceInput';
import CustomSourceInput from '../CustomSourceInput';

export default function WebInput({
  setIsLoading,
  loading,
}: {
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>;
  loading: boolean;
}) {
  const [crawlSubpages, setCrawlSubpages] = useState(false);
  const [maxPages, setMaxPages] = useState(50);

  const {
    inputVal,
    onChangeHandler,
    onBlurHandler,
    submitHandler,
    status,
    setStatus,
    statusMessage,
    isFocused,
    isValid,
    onClose,
    onPasteHandler,
  } = useSourceInput(
    webLinkValidation,
    setIsLoading,
    'web-url',
    false,
    false,
    true,
    crawlSubpages ? { crawl_subpages: true, max_pages: maxPages } : undefined
  );

  return (
    <Flex gap='6' flexDirection='column'>
      <CustomSourceInput
        onCloseHandler={onClose}
        isFocused={isFocused}
        isValid={isValid}
        disabledCheck={Boolean(loading)}
        label='Website Link'
        placeHolder='https://neo4j.com/'
        value={inputVal}
        onChangeHandler={onChangeHandler}
        onBlurHandler={onBlurHandler}
        submitHandler={submitHandler}
        setStatus={setStatus}
        status={status}
        statusMessage={statusMessage}
        id='Website link'
        onPasteHandler={onPasteHandler}
      />
      <Flex flexDirection='row' alignItems='center' gap='4'>
        <Checkbox
          label='Crawl subpages'
          isChecked={crawlSubpages}
          onChange={(e) => setCrawlSubpages(e.target.checked)}
          htmlAttributes={{ id: 'crawl-subpages' }}
        />
        {crawlSubpages && (
          <Flex flexDirection='row' alignItems='center' gap='2'>
            <Typography variant='body-medium'>Max pages:</Typography>
            <TextInput
              htmlAttributes={{
                id: 'max-pages',
                type: 'number',
                min: 1,
                max: 500,
                'aria-label': 'Maximum pages to crawl',
              }}
              value={String(maxPages)}
              isFluid={false}
              onChange={(e) => {
                const val = parseInt(e.target.value, 10);
                if (!isNaN(val) && val > 0) {
                  setMaxPages(val);
                }
              }}
            />
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}
