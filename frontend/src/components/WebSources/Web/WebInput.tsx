import { useCallback, useState } from 'react';
import { Button, Checkbox, Flex, LoadingSpinner, TextInput, Typography } from '@neo4j-ndl/react';
import { v4 as uuidv4 } from 'uuid';
import { webLinkValidation } from '../../../utils/Utils';
import useSourceInput from '../../../hooks/useSourceInput';
import CustomSourceInput from '../CustomSourceInput';
import { urlScanAPI } from '../../../services/URLScan';
import { useCredentials } from '../../../context/UserCredentials';
import { useFileContext } from '../../../context/UsersFiles';
import { getEmbeddingModel } from '../../../utils/EmbeddingConfigUtils';
import { CustomFile } from '../../../types';

interface PathGroup {
  category: string;
  subcategory: string;
  count: number;
  example: string;
}

export default function WebInput({
  setIsLoading,
  loading,
}: {
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>;
  loading: boolean;
}) {
  const [crawlSubpages, setCrawlSubpages] = useState(false);
  const [maxPages, setMaxPages] = useState(50);

  // Path filter state (two-phase flow)
  const [pathGroups, setPathGroups] = useState<PathGroup[]>([]);
  const [selectedCategories, setSelectedCategories] = useState<Set<string>>(new Set());
  const [pendingUrl, setPendingUrl] = useState<string>('');
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [isConfirmLoading, setIsConfirmLoading] = useState(false);

  const { userCredentials } = useCredentials();
  const { setFilesData, model, filesData } = useFileContext();

  const showPathFilter = pathGroups.length > 0;

  // Unique categories from path groups
  const categories = Array.from(
    pathGroups.reduce((map, g) => {
      if (!map.has(g.category)) {
        map.set(g.category, 0);
      }
      map.set(g.category, map.get(g.category)! + g.count);
      return map;
    }, new Map<string, number>())
  ).map(([category, count]) => ({ category, count }));

  const toggleCategory = (category: string) => {
    setSelectedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  };

  const selectAll = () => setSelectedCategories(new Set(categories.map((c) => c.category)));
  const deselectAll = () => setSelectedCategories(new Set());

  const estimatedCount = categories
    .filter((c) => selectedCategories.has(c.category))
    .reduce((sum, c) => sum + c.count, 0);

  const cancelPathFilter = () => {
    setPathGroups([]);
    setSelectedCategories(new Set());
    setPendingUrl('');
  };

  // Confirm: create Document nodes for selected path prefixes
  const confirmPathFilter = useCallback(async () => {
    if (!userCredentials || selectedCategories.size === 0) {
      return;
    }
    setIsConfirmLoading(true);
    try {
      const includePaths = Array.from(selectedCategories).join(',');
      const apiResponse = await urlScanAPI(
        {
          model,
          source_type: 'web-url',
          urlParam: pendingUrl,
          crawl_subpages: true,
          max_pages: maxPages,
          include_paths: includePaths,
        },
        userCredentials
      );

      if (!apiResponse?.data || apiResponse.data.status === 'Failed') {
        return;
      }

      const defaultValues = {
        processingTotalTime: 0,
        status: 'New' as const,
        nodesCount: 0,
        relationshipsCount: 0,
        type: 'TEXT',
        model,
        fileSource: 'web-url',
        processingProgress: undefined,
        retryOption: '',
        retryOptionStatus: false,
        chunkNodeCount: 0,
        chunkRelCount: 0,
        entityNodeCount: 0,
        entityEntityRelCount: 0,
        communityNodeCount: 0,
        communityRelCount: 0,
        token_usage: 0,
        embedding_model: getEmbeddingModel(),
        uploadProgress: 100,
      };

      const copiedFilesData: CustomFile[] = [...filesData];
      if (apiResponse.data.file_name?.length) {
        for (const item of apiResponse.data.file_name) {
          const idx = copiedFilesData.findIndex((f) => f.name === item.fileName);
          if (idx === -1) {
            copiedFilesData.unshift({
              id: uuidv4(),
              name: item.fileName,
              size: item.fileSize,
              sourceUrl: item.url,
              language: item.language,
              urlCategory: item.urlCategory ?? '',
              urlSubcategory: item.urlSubcategory ?? '',
              ...defaultValues,
            });
          } else {
            const existing = copiedFilesData[idx];
            copiedFilesData.splice(idx, 1);
            copiedFilesData.unshift({ ...existing, ...defaultValues });
          }
        }
      }
      setFilesData(copiedFilesData);
    } finally {
      setIsConfirmLoading(false);
      cancelPathFilter();
    }
  }, [userCredentials, selectedCategories, pendingUrl, maxPages, model, filesData]);

  // Override submitHandler for crawl mode: run preview first
  const handleCrawlSubmit = useCallback(
    async (url: string) => {
      if (!userCredentials) {
        return;
      }
      setIsPreviewLoading(true);
      setIsLoading(true);
      try {
        const apiResponse = await urlScanAPI(
          {
            model,
            source_type: 'web-url',
            urlParam: url,
            crawl_subpages: true,
            max_pages: maxPages,
            preview_only: true,
          },
          userCredentials
        );

        if (apiResponse?.data?.status === 'Success' && apiResponse.data.data?.path_groups?.length) {
          const groups: PathGroup[] = apiResponse.data.data.path_groups;
          setPathGroups(groups);
          setPendingUrl(url);
          // Pre-select all categories (user can deselect noise)
          setSelectedCategories(new Set(groups.map((g: PathGroup) => g.category)));
        }
      } finally {
        setIsPreviewLoading(false);
        setIsLoading(false);
      }
    },
    [userCredentials, model, maxPages, setIsLoading]
  );

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
    crawlSubpages ? undefined : undefined
  );

  const handleSubmit = useCallback(
    (url: string) => {
      if (crawlSubpages) {
        handleCrawlSubmit(url);
      } else {
        submitHandler(url);
      }
    },
    [crawlSubpages, handleCrawlSubmit, submitHandler]
  );

  return (
    <Flex gap='6' flexDirection='column'>
      {!showPathFilter && (
        <>
          <CustomSourceInput
            onCloseHandler={onClose}
            isFocused={isFocused}
            isValid={isValid}
            disabledCheck={Boolean(loading) || isPreviewLoading}
            label='Website Link'
            placeHolder='https://neo4j.com/'
            value={inputVal}
            onChangeHandler={onChangeHandler}
            onBlurHandler={onBlurHandler}
            submitHandler={handleSubmit}
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
          {isPreviewLoading && (
            <Flex alignItems='center' gap='2'>
              <LoadingSpinner size='small' />
              <Typography variant='body-medium'>Discovering subpages…</Typography>
            </Flex>
          )}
        </>
      )}

      {showPathFilter && (
        <Flex flexDirection='column' gap='4'>
          <Typography variant='subheading-medium'>Select page sections to import ({estimatedCount} pages)</Typography>
          <Flex flexDirection='row' gap='3'>
            <Button fill='text' size='small' onClick={selectAll}>
              Select all
            </Button>
            <Button fill='text' size='small' onClick={deselectAll}>
              Deselect all
            </Button>
          </Flex>
          <Flex flexDirection='column' gap='2' style={{ maxHeight: '240px', overflowY: 'auto' }}>
            {categories.map(({ category, count }) => (
              <Checkbox
                key={category}
                label={`/${category} (${count})`}
                isChecked={selectedCategories.has(category)}
                onChange={() => toggleCategory(category)}
                htmlAttributes={{ id: `path-${category}` }}
              />
            ))}
          </Flex>
          <Flex flexDirection='row' gap='3'>
            <Button
              onClick={confirmPathFilter}
              isDisabled={selectedCategories.size === 0 || isConfirmLoading}
              isLoading={isConfirmLoading}
            >
              Add {estimatedCount} pages
            </Button>
            <Button fill='outlined' onClick={cancelPathFilter} isDisabled={isConfirmLoading}>
              Cancel
            </Button>
          </Flex>
        </Flex>
      )}
    </Flex>
  );
}
