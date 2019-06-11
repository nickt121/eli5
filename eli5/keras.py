# -*- coding: utf-8 -*-
from __future__ import absolute_import

import numpy as np # type: ignore
import keras # type: ignore
import keras.backend as K # type: ignore
from keras.models import Model # type: ignore
from keras.layers import Layer, Lambda # type: ignore
from keras.preprocessing.image import load_img, img_to_array, array_to_img # type: ignore

from eli5.base import Explanation
from eli5.explain import explain_prediction


DESCRIPTION_KERAS = """Grad-CAM visualization for image input; output is images"""

# note that keras.models.Sequential subclasses keras.models.Model, so we can just register Model
@explain_prediction.register(Model)
def explain_prediction_keras(estimator, doc,
                             target_names=None, # TODO: implement this
                             targets=None,
                             # new parameters:
                             layer=None,
                            ):
    """
    Explain an image prediction of a Keras image classifier.

    See :func:`eli5.explain_prediction` for more information about the ``estimator``,
    ``doc``, ``target_names``, and ``targets`` parameters.

    Parameters
    ----------
    estimator : object
        Instance of a Keras neural network model.
    doc : object
        An input image as a tensor to ``estimator``, for example ``numpy.ndarray``.
    target_names : list, optional
        *`Not Implemented`*.

        Names for classes in the final output layer.
    targets : list[int], optional
        Prediction ID's to focus on.

        *`Currently only the first prediction from the list is explained`*.

        If None, the model is fed the input and its top prediction 
        is taken as target as the target automatically.
    layer : int or str or object, optional
        The activation layer in the model to perform Grad-CAM on,
        a valid keras layer name, layer index, or an instance of keras.layers.Layer.
        
        If None, a suitable layer is attempted to be retrieved (raise ValueError if can not).

    Returns
    -------
    A :class:`base.Explanation` object with the ``image`` and ``heatmap`` attributes set.
    """
    activation_layer = get_activation_layer(estimator, layer)
    predicted = get_target_prediction(estimator, doc, targets)
    
    heatmap = grad_cam(estimator, doc, predicted, activation_layer)
    # TODO: consider renaming 'heatmap' to 'visualization'/'activations' (the output is not yet a heat map)

    # need to insert a 'channel' axis to get a rank 3 image from rank 2
    heatmap = np.expand_dims(heatmap, axis=-1)
    heatmap = array_to_img(heatmap) # -> PIL image
    
    # take the single image from the input 'batch' (rank 4 to rank 3)
    doc = doc[0]
    image = array_to_img(doc) # -> PIL image

    # TODO: return arrays, not images (arrays are more general)
    return Explanation(
        estimator.name, # might want to replace this with something else, eg: estimator.summary()
        description=DESCRIPTION_KERAS,
        error='',
        method='Vanilla Grad-CAM',
        is_regression=False, # TODO: classification vs regression model
        highlight_spaces=None, # might be relevant later when explaining text models
        image=image,
        heatmap=heatmap,
    )


def get_activation_layer(estimator, layer):
    """
    Get an instance of the desired activation layer.

    See :func:`explain_prediction_keras` for description of ``estimator`` and
    ``layer`` parameters.

    Returns
    -------
    A ``keras.layers.Layer`` instance.

    Raises
    ------
    TypeError : ``layer`` is wrong type.
    """        
    if layer is None:
        # Automatically get the layer if not provided
        activation_layer = search_layer_backwards(estimator, is_suitable_activation_layer)
    elif isinstance(layer, Layer):
        activation_layer = layer
    elif isinstance(layer, int):
        # bottom-up horizontal graph traversal
        activation_layer = estimator.get_layer(index=layer)
        # These can raise ValueError if the layer index / name specified is not found
    elif isinstance(layer, str):
        activation_layer = estimator.get_layer(name=layer)
    else:
        raise TypeError('Invalid layer (must be str, int, keras.layers.Layer, or None): %s' % layer)

    # TODO: validate activation_layer dimensions (is it possible to perform Grad-CAM on it?)
    return activation_layer


def search_layer_backwards(estimator, condition):
    """
    Search for a layer in ``estimator`` backwards (starting from output layer),
    checking if the layer is suitable with the callable ``condition``,
    where condition takes ``estimator`` and ``index`` arguments.
    
    Returns
    -------
    layer : object
        A suitable ``keras.layers.Layer`` instance.

    Raises
    ------
    ValueError :
        If suitable layer can not be found.
    """
    # we assume that this is a simple feedforward network
    # linear search in reverse
    i = len(estimator.layers)-1
    while -1 < i and not condition(estimator, i):
        i -= 1
    if -1 < i:
        # linear search succeeded
        return estimator.get_layer(index=i)
    else:
        raise ValueError('Could not find a suitable target layer automatically.')


def is_suitable_activation_layer(estimator, i):
    """
    Check whether
    the layer at index ``i`` matches what is required 
    by ``estimator``.
    
    Matching is done by
    * checking the rank of the layer.

    Returns
    -------
    is suitable : boolean
        whether the layer matches what is needed
    """
    # TODO: experiment with this, using many models and images, to find what works best
    # Some ideas: 
    # check layer type, i.e.: isinstance(l, keras.layers.Conv2D)
    # check layer name
    l = estimator.get_layer(index=i)
    # a check that asks 'can we resize this activation layer over the image?'
    rank = len(l.output_shape)
    required_rank = len(estimator.input_shape)
    return rank == required_rank


def get_target_prediction(model, x, targets):
    """
    Get a prediction ID from ``targets``.

    See documentation of ``explain_prediction_keras`` for explanation of ``targets``.
    
    Returns
    -------
    prediction id : int
    """
    # TODO: take in a single target as well, not just a list, 
    # consider changing signature / types for explain_prediction generic function
    # TODO: need to find a way to show the label for the passed prediction 
    # as well as its probability

    # TODO: maybe do the sum / loss in this function instead of grad_cam. Return a tensor.
    # This would be consistent with what is done in https://github.com/ramprs/grad-cam/blob/master/misc/utils.lua
    # https://github.com/ramprs/grad-cam/blob/master/classification.lua
    # https://github.com/torch/nn/blob/master/doc/module.md
    if isinstance(targets, list):
        # take the first prediction from the list
        predicted_idx = targets[0]
        # TODO: use all predictions in the list
        # TODO: validate list contents
    elif targets is None:
        predictions = model.predict(x)
        predicted_idx = np.argmax(predictions)
        print('Taking top prediction: %d' % predicted_idx)
        # TODO: append this to description / log instead of printing
    else:
        raise ValueError('Invalid argument "targets" (must be list or None): %s' % targets)
    return predicted_idx


def grad_cam(estimator, image, prediction_index, activation_layer):
    """
    Generate a heatmap using Gradient-weighted Class Activation Mapping (Grad-CAM).

    Credits for implementation
    * Jacob Gildenblat for "https://github.com/jacobgil/keras-grad-cam".
    * Author of "https://github.com/PowerOfCreation/keras-grad-cam" for fixes to Jacob's implementation.
    * Kotikalapudi, Raghavendra and contributors for "https://github.com/raghakot/keras-vis".
    
    Returns
    -------
    heatmap : object
        A numpy.ndarray localization map.
    """
    # FIXME: this assumes that we are doing classification
    # FIXME: we also explicitly assume that we are dealing with images

    # Get required terms
    weights, activations, grads_val = grad_cam_backend(estimator, image, prediction_index, activation_layer)

    # Perform a weighted linear combination
    spatial_shape = activations.shape[:2]
    lmap = np.zeros(spatial_shape, dtype=np.float32)
    for i, w in enumerate(weights):
        # weight * single activation map
        # add to the entire map (linear combination), NOT pixel by pixel
        lmap += w * activations[..., i]

    lmap = np.maximum(lmap, 0) # ReLU

    lmap = lmap / np.max(lmap) # -> [0, 1] ndarray
    return lmap


def grad_cam_backend(estimator, image, prediction_index, activation_layer):
    """
    Compute the terms required by the Grad-CAM formula.

    Returns
    -------
    (weights, activations, gradients) : tuple[object]
        Values of variables.
    """
    output = estimator.output
    score = output[:, prediction_index]
    # output of target layer, i.e. activation maps of a convolutional layer
    activation_output = activation_layer.output 

    grads = K.gradients(score, [activation_output])
    # FIXME: this might have issues
    # See https://github.com/jacobgil/keras-grad-cam/issues/17
    # grads = [grad if grad is not None else K.zeros_like(var) 
    #         for (var, grad) in zip(xs, grads)]
    grads = grads[0]
    grads =  K.l2_normalize(grads) # this seems to make the heatmap less noisy
    evaluate = K.function([estimator.input], [activation_output, grads])

    activations, grads_val = evaluate([image]) # evaluate the graph / do computations
    activations = activations[0, ...]
    grads_val = grads_val[0, ...]
    weights = np.mean(grads_val, axis=(0, 1)) # Global Average Pooling
    # TODO: replace numpy operations with keras backend operations, i.e. K.mean
    return weights, activations, grads_val


def image_from_path(img_path, image_shape=None):
    """
    Load a single image from disk, with an optional resize.

    Parameters
    ----------
    img_path : str
        Path to a single image file.
    image_shape : tuple[int], optional
        A (height, width) tuple that indicates the dimensions that the 
        image is to be resized to.

    Returns
    -------
    doc : object
        A numpy.ndarray representing the image as input to a model.
    """
    # TODO: Take in PIL image object, or an array
    # "pipeline": path str -> PIL image -> numpy array
    # TODO: multiple images
    im = load_img(img_path, target_size=image_shape)
    x = img_to_array(im)

    # we need to insert an axis at the 0th position to indicate the batch size (required by the model's input)
    x = np.expand_dims(x, axis=0)
    return x