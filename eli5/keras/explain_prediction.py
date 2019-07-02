# -*- coding: utf-8 -*-
from __future__ import absolute_import
from typing import Union, Optional, Callable, Tuple, List

import numpy as np # type: ignore
import keras # type: ignore
import keras.backend as K # type: ignore
from keras.models import Model # type: ignore
from keras.layers import Layer # type: ignore

from eli5.base import (
    Explanation, 
    TargetExplanation, 
    empty_feature_weights,
    WeightedSpans,
    DocWeightedSpans,
)
from eli5.explain import explain_prediction
from .gradcam import gradcam, gradcam_backend, compute_weights


DESCRIPTION_KERAS = """Grad-CAM visualization for image classification; 
output is explanation object that contains input image 
and heatmap image for a target.
"""

# note that keras.models.Sequential subclasses keras.models.Model
@explain_prediction.register(Model)
def explain_prediction_keras(estimator, # type: Model
                             doc, # type: np.ndarray
                             target_names=None,
                             targets=None, # type: Optional[list]
                             layer=None, # type: Optional[Union[int, str, Layer]]
                             tokens=None, # tokens corresponding to doc (will be used for formatting)
                                     # should be WITHOUT padding?
                                     # FIXME: move this to formatter func?
                                     # FIXME: separator chars?
                             pad_idx=None, # starting padding id
                            ):
    # type: (...) -> Explanation
    # FIXME: in docs rendered param order is "type, required (paramname)", should be other way around
    """
    Explain the prediction of a Keras image classifier.

    We make two explicit assumptions
        * The input is images.
        * The model's task is classification, i.e. final output is class scores.

    See :func:`eli5.explain_prediction` for more information about the ``estimator``,
    ``doc``, ``target_names``, and ``targets`` parameters.

    
    :param estimator `keras.models.Model`:
        Instance of a Keras neural network model, 
        whose predictions are to be explained.


    :param doc `numpy.ndarray`:
        An input image as a tensor to ``estimator``, 
        from which prediction will be done and explained.

        For example a ``numpy.ndarray``.

        The tensor must be of suitable shape for the ``estimator``. 

        For example, some models require input images to be 
        rank 4 in format `(batch_size, dims, ..., channels)` (channels last)
        or `(batch_size, channels, dims, ...)` (channels first), 
        where `dims` is usually in order `height, width`
        and `batch_size` is 1 for a single image.

        Check ``estimator.input_shape`` to confirm the required dimensions of the input tensor.


        :raises TypeError: if ``doc`` is not a numpy array.
        :raises ValueError: if ``doc`` shape does not match.

    :param target_names `list, optional`:
        *Not Implemented*.

        Names for classes in the final output layer.

    :param targets `list[int], optional`:
        Prediction ID's to focus on.

        *Currently only the first prediction from the list is explained*. 
        The list must be length one.

        If None, the model is fed the input image and its top prediction 
        is taken as the target automatically.


        :raises ValueError: if targets is a list with more than one item.
        :raises TypeError: if targets is not list or None.

    :param layer `int or str or keras.layers.Layer, optional`:
        The activation layer in the model to perform Grad-CAM on,
        a valid keras layer name, layer index, or an instance of a Keras layer.
        
        If None, a suitable layer is attempted to be retrieved. 
        See :func:`eli5.keras._search_layer_backwards` for details.


        :raises TypeError: if ``layer`` is not None, str, int, or keras.layers.Layer instance.
        :raises ValueError: if suitable layer can not be found.


    Returns
    -------
    expl : eli5.base.Explanation
        An ``Explanation`` object with the following attributes set (some inside ``targets``)
            * ``image`` a Pillow image with mode RGBA.
            * ``heatmap`` a rank 2 numpy array with the localization map values.
            * ``target`` ID of target class.
            * ``proba`` output for target class for ``softmax`` or ``sigmoid`` outputs.
            * ``score`` output for target class for other activations.
    """
    _validate_doc(estimator, doc)
    activation_layer = _get_activation_layer(estimator, layer)
    # TODO: maybe do the sum / loss calculation in this function and pass it to gradcam.
    # This would be consistent with what is done in
    # https://github.com/ramprs/grad-cam/blob/master/misc/utils.lua
    # and https://github.com/ramprs/grad-cam/blob/master/classification.lua
    values = gradcam_backend(estimator, doc, targets, activation_layer)
    activations, grads, predicted_idx, predicted_val = values
    # FIXME: hardcoding for conv layers, i.e. their shapes
    weights = compute_weights(activations)
    heatmap = gradcam(weights, activations)

    # classify predicted_val as either a probability or a score
    proba = None
    score = None
    if _outputs_proba(estimator):
        proba = predicted_val
    else:
        score = predicted_val 

    # specific for images
    # doc = doc[0] # rank 4 batch -> rank 3 single image
    # image = keras.preprocessing.image.array_to_img(doc) # -> RGB Pillow image
    # image = image.convert(mode='RGBA')
    image = None # hard code for text

    # TODO: cut off padding from text
    # what about images? pass 2 tuple?
    if pad_idx is None:
        pass
    else:
        pass

    feature_weights = empty_feature_weights
    if image is not None:
        weighted_spans = None
    else:
        spans = []
        running = 0
        for (token, weight) in zip(tokens, heatmap): # FIXME: weight can be renamed
            i = running
            N = len(token)
            j = i+N
            span = tuple([token, [tuple([i, j])], weight])
            running = j+1 # exclude space
            # print(N, token, weight, i, j)
            spans.append(span)
        document = ' '.join(tokens)
        weighted_spans = WeightedSpans([
            DocWeightedSpans(document, spans=spans)
        ]) # why list?

    return Explanation(
        estimator.name,
        description=DESCRIPTION_KERAS,
        error='',
        method='Grad-CAM',
        image=image, # RGBA Pillow image
        targets=[TargetExplanation(
            predicted_idx,
            feature_weights=feature_weights,
            weighted_spans=weighted_spans,
            proba=proba,
            score=score,
            heatmap=heatmap, # 2D [0, 1] numpy array
        )],
        is_regression=False, # might be relevant later when explaining for regression tasks
        highlight_spaces=None, # might be relevant later when explaining text models
    )


def _validate_doc(estimator, doc):
    # type: (Model, np.ndarray) -> None
    """
    Check that the input ``doc`` is suitable for ``estimator``.
    """
    # FIXME: is this validation worth it? Just use Keras validation?
    # Do we make any extra assumptions about doc?
    # https://github.com/keras-team/keras/issues/1641
    if not isinstance(doc, np.ndarray):
        raise TypeError('doc must be a numpy.ndarray, got: {}'.format(doc))
    input_sh = estimator.input_shape
    doc_sh = doc.shape
    if len(input_sh) == 4:
        # FIXME: need better check for images (an attribute)
        # rank 4 with (batch, ...) shape
        # check that we have only one image (batch size 1)
        single_batch = (1,) + input_sh[1:]
        if doc_sh != single_batch:
            raise ValueError('Batch size does not match (must be 1). ' 
                             'doc must be of shape: {}, '
                             'got: {}'.format(single_batch, doc_sh))
    else:
        # other shapes
        if not eq_shapes(input_sh, doc_sh):
            raise ValueError('Input and doc shapes do not match. '
                             'input: {}, doc: {}'.format(input_sh, doc_sh))


def eq_shapes(required, other):
    """
    Check that ``other`` shape satisfies shape of ``required``

    For example::
        eq_shapes((None, 20), (1, 20)) # -> True
    """
    matching = [(d1 == d2) # check that same number of dims 
            if (d1 is not None) # if required takes a specific shape for a dim (not None)
            else (1 <= d2) # else just check that the other shape has a valid shape for a dim
            for d1, d2 in zip(required, other)]
    return all(matching)


def _get_activation_layer(estimator, layer):
    # type: (Model, Union[None, int, str, Layer]) -> Layer
    """
    Get an instance of the desired activation layer in ``estimator``,
    as specified by ``layer``.
    """
    # PR FIXME: Would be good to include retrieved layer as an attribute
    if layer is None:
        # Automatically get the layer if not provided
        # TODO: search forwards for text models
        activation_layer = _search_layer_backwards(estimator, _is_suitable_activation_layer)
        return activation_layer

    if isinstance(layer, Layer):
        activation_layer = layer
    # get_layer() performs a bottom-up horizontal graph traversal
    # it can raise ValueError if the layer index / name specified is not found
    elif isinstance(layer, int):
        activation_layer = estimator.get_layer(index=layer)
    elif isinstance(layer, str):
        activation_layer = estimator.get_layer(name=layer)
    else:
        raise TypeError('Invalid layer (must be str, int, keras.layers.Layer, or None): %s' % layer)

    if _is_suitable_activation_layer(estimator, activation_layer):
        # final validation step
        # FIXME: this should not be done for text
        # PR FIXME: decouple layer search (no arg) vs simple layer retrieval (arg)
        return activation_layer
    else:
        raise ValueError('Can not perform Grad-CAM on the retrieved activation layer')


def _search_layer_backwards(estimator, condition):
    # type: (Model, Callable[[Model, int], bool]) -> Layer
    """
    Search for a layer in ``estimator``, backwards (starting from the output layer),
    checking if the layer is suitable with the callable ``condition``,
    """
    # linear search in reverse through the flattened layers
    for layer in estimator.layers[::-1]:
        if condition(estimator, layer):
            # linear search succeeded
            return layer
    # linear search ended with no results
    raise ValueError('Could not find a suitable target layer automatically.')        


def _is_suitable_activation_layer(estimator, layer):
    # type: (Model, Layer) -> bool
    """
    Check whether the layer ``layer`` matches what is required 
    by ``estimator`` to do Grad-CAM on ``layer``.
    Returns a boolean.
    
    Matching Criteria:
        * Rank of the layer's output tensor.
    """
    # TODO: experiment with this, using many models and images, to find what works best
    # Some ideas: 
    # check layer type, i.e.: isinstance(l, keras.layers.Conv2D)
    # check layer name

    # a check that asks "can we resize this activation layer over the image?"

    # text FIXME: matching rank is not a good way to check if layer is valid
    # input has rank 2 (sequence)
    # output often has rank 2 (batch and units)
    # input wrpt output???

    rank = len(layer.output_shape)
    if rank == 4:
        # only for 'images'
        required_rank = len(estimator.input_shape)
        return rank == required_rank
    else:
        # no check yet
        return True


def _outputs_proba(estimator):
    # type: (Model) -> bool
    """
    Check whether ``estimator`` gives probabilities as its output.
    """
    output_layer = estimator.get_layer(index=-1)
    # we check if the network's output is put through softmax
    # we assume that only softmax can output 'probabilities'

    try:
        actv = output_layer.activation 
    except AttributeError:
        # output layer does not support activation function
        return False
    else:
        return (actv is keras.activations.softmax or 
                actv is keras.activations.sigmoid)